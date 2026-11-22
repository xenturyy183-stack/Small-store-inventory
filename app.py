import json
import math
import os
import threading
import webbrowser
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for


INVENTORY_FILE = Path(__file__).with_name("inventory.json")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "local-development-key")


def load_items():
    try:
        with INVENTORY_FILE.open("r", encoding="utf-8") as inventory_file:
            items = json.load(inventory_file)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not load inventory: {error}") from error

    if not isinstance(items, list):
        raise RuntimeError("The inventory file has an invalid format.")
    return items


def save_items(items):
    try:
        with INVENTORY_FILE.open("w", encoding="utf-8") as inventory_file:
            json.dump(items, inventory_file, indent=2)
    except OSError as error:
        raise RuntimeError(f"Could not save inventory: {error}") from error


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


@app.route("/", methods=["GET"])
def index():
    try:
        items = load_items()
    except RuntimeError as error:
        flash(str(error), "error")
        items = []

    search = request.args.get("search", "").strip().lower()
    if search:
        items = [item for item in items if search in item["name"].lower()]

    total_items = sum(item["quantity"] for item in items)
    total_value = sum(item["quantity"] * item["price"] for item in items)
    return render_template(
        "index.html",
        items=items,
        search=request.args.get("search", "").strip(),
        total_items=total_items,
        total_value=total_value,
    )


@app.post("/add")
def add_item():
    try:
        name, quantity, price = read_item_form()
        items = load_items()
        items.append({"name": name, "quantity": quantity, "price": price})
        save_items(items)
        flash(f"Added {name}.", "success")
    except (ValueError, RuntimeError) as error:
        flash(str(error), "error")
    return redirect(url_for("index"))


@app.post("/update")
def update_item():
    try:
        name, quantity, price = read_item_form()
        items = load_items()

        for item in items:
            if item["name"] == name:
                item["quantity"] = quantity
                item["price"] = price
                save_items(items)
                flash(f"Updated {name}.", "success")
                return redirect(url_for("index"))

        flash(f"No item named {name}.", "error")
    except (ValueError, RuntimeError) as error:
        flash(str(error), "error")
    return redirect(url_for("index"))


@app.post("/delete")
def delete_item():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Enter an item name.", "error")
        return redirect(url_for("index"))

    try:
        items = load_items()
        for index, item in enumerate(items):
            if item["name"] == name:
                del items[index]
                save_items(items)
                flash(f"Deleted {name}.", "success")
                return redirect(url_for("index"))

        flash(f"No item named {name}.", "error")
    except RuntimeError as error:
        flash(str(error), "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1, webbrowser.open, args=(url,)).start()
    app.run(
        host="127.0.0.1",
        port=port,
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
