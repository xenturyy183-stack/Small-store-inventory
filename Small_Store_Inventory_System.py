import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


INVENTORY_FILE = Path(__file__).with_name("inventory.json")
Items = []


def add_item(item_name, item_quantity, item_price):
    Items.append({
        "name": item_name,
        "quantity": item_quantity,
        "price": item_price,
    })


def view_items():
    return Items


def update_item(item_name, new_quantity, new_price):
    for item in Items:
        if item["name"] == item_name:
            item["quantity"] = new_quantity
            item["price"] = new_price
            return True
    return False


def delete_item(item_name):
    for index, item in enumerate(Items):
        if item["name"] == item_name:
            del Items[index]
            return True
    return False


def search_item(item_name):
    for item in Items:
        if item["name"] == item_name:
            return item
    return None


def load_items():
    try:
        with INVENTORY_FILE.open("r", encoding="utf-8") as inventory_file:
            items = json.load(inventory_file)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        messagebox.showerror("Could not load inventory", str(error))
        return []

    if not isinstance(items, list):
        messagebox.showerror("Could not load inventory", "The inventory file has an invalid format.")
        return []
    return items


def create_app():
    global Items

    root = tk.Tk()
    root.title("Small Store Inventory")
    root.geometry("820x560")
    root.minsize(680, 460)
    Items = load_items()

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Treeview", rowheight=28)
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    container = ttk.Frame(root, padding=20)
    container.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)
    container.rowconfigure(3, weight=1)

    ttk.Label(container, text="Small Store Inventory", font=("Segoe UI", 20, "bold")).grid(
        row=0, column=0, sticky="w", pady=(0, 16)
    )

    form = ttk.Frame(container)
    form.grid(row=1, column=0, sticky="ew", pady=(0, 12))
    for column in range(3):
        form.columnconfigure(column, weight=1)

    name_var = tk.StringVar()
    quantity_var = tk.StringVar()
    price_var = tk.StringVar()

    ttk.Label(form, text="Item name").grid(row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Label(form, text="Quantity").grid(row=0, column=1, sticky="w", padx=(0, 10))
    ttk.Label(form, text="Unit price").grid(row=0, column=2, sticky="w")
    ttk.Entry(form, textvariable=name_var).grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(4, 0))
    ttk.Entry(form, textvariable=quantity_var).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(4, 0))
    ttk.Entry(form, textvariable=price_var).grid(row=1, column=2, sticky="ew", pady=(4, 0))

    table_frame = ttk.Frame(container)
    table_frame.grid(row=3, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)

    columns = ("name", "quantity", "price", "stock_value")
    inventory_table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
    inventory_table.heading("name", text="Item")
    inventory_table.heading("quantity", text="Quantity")
    inventory_table.heading("price", text="Unit price")
    inventory_table.heading("stock_value", text="Stock value")
    inventory_table.column("name", width=280, anchor="w")
    inventory_table.column("quantity", width=110, anchor="e")
    inventory_table.column("price", width=140, anchor="e")
    inventory_table.column("stock_value", width=160, anchor="e")
    inventory_table.grid(row=0, column=0, sticky="nsew")

    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=inventory_table.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    inventory_table.configure(yscrollcommand=scrollbar.set)

    status_var = tk.StringVar(value="Ready")
    ttk.Label(container, textvariable=status_var).grid(row=4, column=0, sticky="w", pady=(10, 0))

    def save_items():
        try:
            with INVENTORY_FILE.open("w", encoding="utf-8") as inventory_file:
                json.dump(Items, inventory_file, indent=2)
        except OSError as error:
            messagebox.showerror("Could not save inventory", str(error), parent=root)

    def refresh_table():
        inventory_table.delete(*inventory_table.get_children())
        for index, item in enumerate(view_items()):
            inventory_table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item["name"],
                    item["quantity"],
                    f"${item['price']:.2f}",
                    f"${item['quantity'] * item['price']:.2f}",
                ),
            )

    def read_form():
        item_name = name_var.get().strip()
        if not item_name:
            raise ValueError("Enter an item name.")
        try:
            item_quantity = int(quantity_var.get())
        except ValueError as error:
            raise ValueError("Quantity must be a whole number.") from error
        try:
            item_price = float(price_var.get())
        except ValueError as error:
            raise ValueError("Price must be a number.") from error
        if item_quantity < 0:
            raise ValueError("Quantity cannot be negative.")
        if not math.isfinite(item_price) or item_price < 0:
            raise ValueError("Price must be a non-negative number.")
        return item_name, item_quantity, item_price

    def add_from_form():
        try:
            item_name, item_quantity, item_price = read_form()
        except ValueError as error:
            messagebox.showerror("Invalid item", str(error), parent=root)
            return
        add_item(item_name, item_quantity, item_price)
        save_items()
        refresh_table()
        status_var.set(f"Added {item_name}")

    def update_from_form():
        try:
            item_name, item_quantity, item_price = read_form()
        except ValueError as error:
            messagebox.showerror("Invalid item", str(error), parent=root)
            return
        if not update_item(item_name, item_quantity, item_price):
            messagebox.showinfo("Item not found", f"No item named {item_name}.", parent=root)
            return
        save_items()
        refresh_table()
        status_var.set(f"Updated {item_name}")

    def delete_from_form():
        item_name = name_var.get().strip()
        if not item_name:
            messagebox.showerror("Delete item", "Enter the item name to delete.", parent=root)
            return
        if not search_item(item_name):
            messagebox.showinfo("Item not found", f"No item named {item_name}.", parent=root)
            return
        confirmed = messagebox.askyesno(
            "Confirm deletion",
            f"Delete {item_name} from the inventory?",
            parent=root,
        )
        if not confirmed:
            return
        delete_item(item_name)
        save_items()
        refresh_table()
        status_var.set(f"Deleted {item_name}")

    def search_from_form():
        item_name = name_var.get().strip()
        if not item_name:
            messagebox.showerror("Search", "Enter an item name.", parent=root)
            return
        item = search_item(item_name)
        if item is None:
            status_var.set(f"No item named {item_name}")
            return
        item_index = Items.index(item)
        inventory_table.selection_set(str(item_index))
        inventory_table.focus(str(item_index))
        inventory_table.see(str(item_index))
        status_var.set(
            f"Found {item_name}: stock value ${item['quantity'] * item['price']:.2f}"
        )

    def clear_form():
        name_var.set("")
        quantity_var.set("")
        price_var.set("")
        status_var.set("Ready")

    actions = ttk.Frame(container)
    actions.grid(row=2, column=0, sticky="w", pady=(0, 14))
    ttk.Button(actions, text="Add item", command=add_from_form).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(actions, text="Update item", command=update_from_form).grid(row=0, column=1, padx=(0, 8))
    ttk.Button(actions, text="Delete item", command=delete_from_form).grid(row=0, column=2, padx=(0, 8))
    ttk.Button(actions, text="Search", command=search_from_form).grid(row=0, column=3, padx=(0, 8))
    ttk.Button(actions, text="Refresh", command=refresh_table).grid(row=0, column=4, padx=(0, 8))
    ttk.Button(actions, text="Clear", command=clear_form).grid(row=0, column=5)

    refresh_table()
    root.mainloop()


if __name__ == "__main__":
    create_app()