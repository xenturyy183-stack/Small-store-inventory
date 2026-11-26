import json
import math
import os
import re
import secrets
import sqlite3
import threading
import webbrowser
from contextlib import closing
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


PROJECT_DIR = Path(__file__).resolve().parent
INVENTORY_FILE = PROJECT_DIR / "inventory.json"
DATABASE_FILE = PROJECT_DIR / "inventory.sqlite3"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    DATABASE=str(DATABASE_FILE),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
)


def connect_db():
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with closing(connect_db()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Other',
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                reorder_level INTEGER NOT NULL DEFAULT 5 CHECK (reorder_level >= 0),
                item_code TEXT,
                price REAL NOT NULL CHECK (price >= 0)
            );
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
                item_name TEXT NOT NULL,
                movement_type TEXT NOT NULL,
                quantity_change INTEGER NOT NULL CHECK (quantity_change != 0),
                quantity_after INTEGER NOT NULL CHECK (quantity_after >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        item_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(items)")
        }
        if "category" not in item_columns:
            connection.execute(
                "ALTER TABLE items ADD COLUMN category TEXT NOT NULL DEFAULT 'Other'"
            )
        if "reorder_level" not in item_columns:
            connection.execute(
                """
                ALTER TABLE items ADD COLUMN reorder_level INTEGER NOT NULL
                DEFAULT 5 CHECK (reorder_level >= 0)
                """
            )
        if "item_code" not in item_columns:
            connection.execute("ALTER TABLE items ADD COLUMN item_code TEXT")
        connection.execute(
            """
            UPDATE items
            SET item_code = 'SKU-' || printf('%06d', id)
            WHERE item_code IS NULL OR item_code = ''
            """
        )
        duplicate_groups = connection.execute(
            """
            SELECT user_id, name COLLATE NOCASE AS normalized_name
            FROM items
            GROUP BY user_id, name COLLATE NOCASE
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for group in duplicate_groups:
            duplicates = connection.execute(
                """
                SELECT id, quantity FROM items
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                ORDER BY id
                """,
                (group["user_id"], group["normalized_name"]),
            ).fetchall()
            keep_id = duplicates[-1]["id"]
            total_quantity = sum(item["quantity"] for item in duplicates)
            duplicate_ids = [item["id"] for item in duplicates[:-1]]
            connection.execute(
                "UPDATE items SET quantity = ? WHERE id = ?",
                (total_quantity, keep_id),
            )
            connection.executemany(
                "DELETE FROM items WHERE id = ?",
                [(item_id,) for item_id in duplicate_ids],
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_item_name_per_user
            ON items (user_id, name COLLATE NOCASE)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_item_code
            ON items (item_code)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS stock_movements_user_history
            ON stock_movements (user_id, id DESC)
            """
        )
        connection.commit()


def csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


@app.before_request
def protect_post_requests():
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        stored_token = session.get("_csrf_token", "")
        if not stored_token or not secrets.compare_digest(submitted_token, stored_token):
            return "Invalid or missing form token. Reload the page and try again.", 400

    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        with closing(connect_db()) as connection:
            g.user = connection.execute(
                "SELECT id, username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if g.user is None:
            session.clear()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def import_legacy_inventory(connection, user_id):
    try:
        with INVENTORY_FILE.open("r", encoding="utf-8") as inventory_file:
            items = json.load(inventory_file)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read the existing inventory: {error}") from error

    if not isinstance(items, list):
        raise RuntimeError("The existing inventory file has an invalid format.")

    for item in items:
        if (
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("quantity"), int)
            and not isinstance(item.get("quantity"), bool)
            and isinstance(item.get("price"), (int, float))
            and math.isfinite(item["price"])
            and item["quantity"] >= 0
            and item["price"] >= 0
        ):
            existing_item = connection.execute(
                """
                SELECT id, quantity FROM items
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                """,
                (user_id, item["name"]),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO items
                    (user_id, name, category, quantity, reorder_level, price)
                VALUES (?, ?, ?, ?, 5, ?)
                ON CONFLICT(user_id, name COLLATE NOCASE) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    quantity = excluded.quantity,
                    reorder_level = excluded.reorder_level,
                    price = excluded.price
                """,
                (user_id, item["name"], "Other", item["quantity"], item["price"]),
            )
            item_id = existing_item["id"] if existing_item else cursor.lastrowid
            if existing_item is None:
                connection.execute(
                    "UPDATE items SET item_code = ? WHERE id = ?",
                    (format_item_code(item_id), item_id),
                )
            quantity_change = item["quantity"]
            movement_type = "Initial stock"
            if existing_item is not None:
                quantity_change -= existing_item["quantity"]
                movement_type = (
                    "Stock increased" if quantity_change > 0 else "Stock decreased"
                )
            record_stock_movement(
                connection,
                user_id,
                item_id,
                item["name"],
                movement_type,
                quantity_change,
                item["quantity"],
            )


def read_item_form():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    name = name[:1].upper() + name[1:]
    category = category[:1].upper() + category[1:]
    quantity_text = request.form.get("quantity", "").strip()
    reorder_level_text = request.form.get("reorder_level", "").strip()
    price_text = request.form.get("price", "").strip()

    if not name:
        raise ValueError("Enter an item name.")
    if not category:
        raise ValueError("Enter an item category.")
    if len(category) > 50:
        raise ValueError("Category must be 50 characters or fewer.")

    try:
        quantity = int(quantity_text)
    except ValueError as error:
        raise ValueError("Quantity must be a whole number.") from error

    try:
        reorder_level = int(reorder_level_text)
    except ValueError as error:
        raise ValueError("Reorder level must be a whole number.") from error

    try:
        price = float(price_text)
    except ValueError as error:
        raise ValueError("Price must be a number.") from error

    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    if reorder_level < 0:
        raise ValueError("Reorder level cannot be negative.")
    if not math.isfinite(price) or price < 0:
        raise ValueError("Price must be a non-negative number.")

    return name, category, quantity, reorder_level, price


def record_stock_movement(
    connection, user_id, item_id, item_name, movement_type, quantity_change, quantity_after
):
    if quantity_change == 0:
        return
    connection.execute(
        """
        INSERT INTO stock_movements
            (user_id, item_id, item_name, movement_type, quantity_change, quantity_after)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, item_id, item_name, movement_type, quantity_change, quantity_after),
    )


def format_item_code(item_id):
    return f"SKU-{item_id:06d}"


@app.get("/register")
def register():
    if g.user is not None:
        return redirect(url_for("index"))
    return render_template("register.html")


@app.post("/register")
def create_account():
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")

    if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
        flash("Username must be 3–32 characters: letters, numbers, or underscores.", "error")
        return render_template("register.html"), 400
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("register.html"), 400

    try:
        with closing(connect_db()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            first_user = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            if first_user:
                import_legacy_inventory(connection, cursor.lastrowid)
            connection.commit()
    except sqlite3.IntegrityError:
        flash("That username is already taken.", "error")
        return render_template("register.html"), 400
    except (OSError, RuntimeError, sqlite3.Error) as error:
        app.logger.exception("Could not create account")
        flash(f"Could not create account: {error}", "error")
        return render_template("register.html"), 500

    session.clear()
    session["user_id"] = cursor.lastrowid
    csrf_token()
    flash("Account created. Welcome!", "success")
    return redirect(url_for("index"))


@app.get("/login")
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/login")
def sign_in():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    with closing(connect_db()) as connection:
        user = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        flash("Invalid username or password.", "error")
        return render_template("login.html"), 401

    session.clear()
    session["user_id"] = user["id"]
    csrf_token()
    return redirect(url_for("index"))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.get("/movements")
@login_required
def movements():
    with closing(connect_db()) as connection:
        stock_movements = connection.execute(
            """
            SELECT item_name, movement_type, quantity_change, quantity_after, created_at
            FROM stock_movements
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (g.user["id"],),
        ).fetchall()
    return render_template(
        "movements.html",
        movements=stock_movements,
        username=g.user["username"],
    )


@app.get("/")
@login_required
def index():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    with closing(connect_db()) as connection:
        conditions = ["user_id = ?"]
        parameters = [g.user["id"]]
        if search:
            conditions.append("name LIKE ? ESCAPE '\\'")
            parameters.append(
                "%"
                + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
        if category:
            conditions.append("category = ? COLLATE NOCASE")
            parameters.append(category)
        items = connection.execute(
            f"""
            SELECT id, name, category, quantity, reorder_level, item_code, price
            FROM items
            WHERE {" AND ".join(conditions)}
            ORDER BY id
            """,
            parameters,
        ).fetchall()
        categories = connection.execute(
            """
            SELECT MIN(category) AS category FROM items
            WHERE user_id = ?
            GROUP BY category COLLATE NOCASE
            ORDER BY category COLLATE NOCASE
            """,
            (g.user["id"],),
        ).fetchall()

    total_items = sum(item["quantity"] for item in items)
    total_value = sum(item["quantity"] * item["price"] for item in items)
    reorder_count = sum(
        item["quantity"] <= item["reorder_level"] for item in items
    )
    return render_template(
        "index.html",
        items=items,
        search=search,
        category=category,
        categories=categories,
        total_items=total_items,
        total_value=total_value,
        reorder_count=reorder_count,
        username=g.user["username"],
    )


@app.post("/add")
@login_required
def add_item():
    try:
        name, category, quantity, reorder_level, price = read_item_form()
        with closing(connect_db()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_item = connection.execute(
                """
                SELECT id, quantity FROM items
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                """,
                (g.user["id"], name),
            ).fetchone()
            if existing_item:
                connection.execute(
                    """
                    UPDATE items
                    SET name = ?, category = ?, quantity = ?, reorder_level = ?, price = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        name,
                        category,
                        quantity,
                        reorder_level,
                        price,
                        existing_item["id"],
                        g.user["id"],
                    ),
                )
                quantity_change = quantity - existing_item["quantity"]
                movement_type = (
                    "Stock increased" if quantity_change > 0 else "Stock decreased"
                )
                record_stock_movement(
                    connection,
                    g.user["id"],
                    existing_item["id"],
                    name,
                    movement_type,
                    quantity_change,
                    quantity,
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO items
                        (user_id, name, category, quantity, reorder_level, price)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (g.user["id"], name, category, quantity, reorder_level, price),
                )
                connection.execute(
                    "UPDATE items SET item_code = ? WHERE id = ?",
                    (format_item_code(cursor.lastrowid), cursor.lastrowid),
                )
                record_stock_movement(
                    connection,
                    g.user["id"],
                    cursor.lastrowid,
                    name,
                    "Initial stock",
                    quantity,
                    quantity,
                )
            connection.commit()
        if existing_item:
            flash(f"Updated existing item {name}.", "success")
        else:
            flash(f"Added {name}.", "success")
    except ValueError as error:
        flash(str(error), "error")
    except sqlite3.Error:
        app.logger.exception("Could not add inventory item")
        flash("Could not save the item. Please try again.", "error")
    return redirect(url_for("index"))


@app.post("/update/<int:item_id>")
@login_required
def update_item(item_id):
    try:
        name, category, quantity, reorder_level, price = read_item_form()
        with closing(connect_db()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_item = connection.execute(
                "SELECT id, name, quantity FROM items WHERE id = ? AND user_id = ?",
                (item_id, g.user["id"]),
            ).fetchone()
            if current_item is None:
                flash("Item not found in your inventory.", "error")
                return redirect(url_for("index"))
            duplicate = connection.execute(
                """
                SELECT id FROM items
                WHERE user_id = ? AND name = ? COLLATE NOCASE AND id != ?
                """,
                (g.user["id"], name, item_id),
            ).fetchone()
            if duplicate:
                flash("Another item already uses that name. Edit that item instead.", "error")
                return redirect(url_for("index"))
            result = connection.execute(
                """
                UPDATE items
                SET name = ?, category = ?, quantity = ?, reorder_level = ?, price = ?
                WHERE id = ? AND user_id = ?
                """,
                (name, category, quantity, reorder_level, price, item_id, g.user["id"]),
            )
            quantity_change = quantity - current_item["quantity"]
            movement_type = (
                "Stock increased" if quantity_change > 0 else "Stock decreased"
            )
            record_stock_movement(
                connection,
                g.user["id"],
                item_id,
                name,
                movement_type,
                quantity_change,
                quantity,
            )
            connection.commit()
        if result.rowcount:
            flash(f"Updated {name}.", "success")
        else:
            flash("Item not found in your inventory.", "error")
    except ValueError as error:
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        flash("Another item already uses that name. Edit that item instead.", "error")
    except sqlite3.Error:
        app.logger.exception("Could not update inventory item")
        flash("Could not update the item. Please try again.", "error")
    return redirect(url_for("index"))


@app.post("/delete/<int:item_id>")
@login_required
def delete_item(item_id):
    try:
        with closing(connect_db()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute(
                "SELECT name, quantity FROM items WHERE id = ? AND user_id = ?",
                (item_id, g.user["id"]),
            ).fetchone()
            if item is None:
                flash("Item not found in your inventory.", "error")
                return redirect(url_for("index"))
            record_stock_movement(
                connection,
                g.user["id"],
                item_id,
                item["name"],
                "Item deleted",
                -item["quantity"],
                0,
            )
            result = connection.execute(
                "DELETE FROM items WHERE id = ? AND user_id = ?",
                (item_id, g.user["id"]),
            )
            connection.commit()
        if result.rowcount:
            flash("Item deleted.", "success")
        else:
            flash("Item not found in your inventory.", "error")
    except sqlite3.Error:
        app.logger.exception("Could not delete inventory item")
        flash("Could not delete the item. Please try again.", "error")
    return redirect(url_for("index"))


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    threading.Timer(
        1,
        webbrowser.open,
        args=(f"http://127.0.0.1:{port}",),
    ).start()
    app.run(
        host="127.0.0.1",
        port=port,
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
