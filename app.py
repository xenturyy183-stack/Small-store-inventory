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
                quantity INTEGER NOT NULL CHECK (quantity >= 0),
                price REAL NOT NULL CHECK (price >= 0)
            );
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
            connection.execute(
                "INSERT INTO items (user_id, name, quantity, price) VALUES (?, ?, ?, ?)",
                (user_id, item["name"], item["quantity"], item["price"]),
            )


def read_item_form():
    name = request.form.get("name", "").strip()
    quantity_text = request.form.get("quantity", "").strip()
    price_text = request.form.get("price", "").strip()

    if not name:
        raise ValueError("Enter an item name.")

    try:
        quantity = int(quantity_text)
    except ValueError as error:
        raise ValueError("Quantity must be a whole number.") from error

    try:
        price = float(price_text)
    except ValueError as error:
        raise ValueError("Price must be a number.") from error

    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    if not math.isfinite(price) or price < 0:
        raise ValueError("Price must be a non-negative number.")

    return name, quantity, price


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


@app.get("/")
@login_required
def index():
    search = request.args.get("search", "").strip()
    with closing(connect_db()) as connection:
        if search:
            items = connection.execute(
                """
                SELECT id, name, quantity, price FROM items
                WHERE user_id = ? AND name LIKE ? ESCAPE '\\'
                ORDER BY id
                """,
                (g.user["id"], "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"),
            ).fetchall()
        else:
            items = connection.execute(
                "SELECT id, name, quantity, price FROM items WHERE user_id = ? ORDER BY id",
                (g.user["id"],),
            ).fetchall()

    total_items = sum(item["quantity"] for item in items)
    total_value = sum(item["quantity"] * item["price"] for item in items)
    return render_template(
        "index.html",
        items=items,
        search=search,
        total_items=total_items,
        total_value=total_value,
        username=g.user["username"],
    )


@app.post("/add")
@login_required
def add_item():
    try:
        name, quantity, price = read_item_form()
        with closing(connect_db()) as connection:
            connection.execute(
                "INSERT INTO items (user_id, name, quantity, price) VALUES (?, ?, ?, ?)",
                (g.user["id"], name, quantity, price),
            )
            connection.commit()
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
        name, quantity, price = read_item_form()
        with closing(connect_db()) as connection:
            result = connection.execute(
                """
                UPDATE items SET name = ?, quantity = ?, price = ?
                WHERE id = ? AND user_id = ?
                """,
                (name, quantity, price, item_id, g.user["id"]),
            )
            connection.commit()
        if result.rowcount:
            flash(f"Updated {name}.", "success")
        else:
            flash("Item not found in your inventory.", "error")
    except ValueError as error:
        flash(str(error), "error")
    except sqlite3.Error:
        app.logger.exception("Could not update inventory item")
        flash("Could not update the item. Please try again.", "error")
    return redirect(url_for("index"))


@app.post("/delete/<int:item_id>")
@login_required
def delete_item(item_id):
    try:
        with closing(connect_db()) as connection:
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
