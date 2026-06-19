import os
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlparse

import psycopg2


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/ennosmart"
)


def connect_db():
    url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    parsed = urlparse(url)

    return psycopg2.connect(
        dbname=parsed.path.lstrip("/"),
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
    )


def get_existing_columns(conn, table_name="users"):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return [row[0] for row in cur.fetchall()]


def load_users():
    conn = connect_db()

    try:
        columns = get_existing_columns(conn, "users")

        if not columns:
            raise Exception("Table users introuvable.")

        wanted_columns = [
            "id",
            "email",
            "full_name",
            "name",
            "role",
            "is_active",
            "password",
            "hashed_password",
            "password_hash",
            "created_at",
        ]

        selected_columns = [col for col in wanted_columns if col in columns]

        if not selected_columns:
            selected_columns = columns

        query = f"""
            SELECT {", ".join(selected_columns)}
            FROM users
            ORDER BY id ASC
        """

        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

        return selected_columns, rows

    finally:
        conn.close()


class UsersApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EnnoSmart - Utilisateurs PostgreSQL")
        self.root.geometry("1100x500")

        title = tk.Label(
            root,
            text="Utilisateurs PostgreSQL - EnnoSmart",
            font=("Arial", 16, "bold"),
        )
        title.pack(pady=10)

        self.refresh_button = tk.Button(
            root,
            text="Recharger",
            command=self.refresh,
            bg="#7c3aed",
            fg="white",
            padx=15,
            pady=5,
        )
        self.refresh_button.pack(pady=5)

        self.tree_frame = tk.Frame(root)
        self.tree_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.tree = None
        self.refresh()

    def refresh(self):
        try:
            columns, rows = load_users()
            self.render_table(columns, rows)

        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    def render_table(self, columns, rows):
        for widget in self.tree_frame.winfo_children():
            widget.destroy()

        self.tree = ttk.Treeview(
            self.tree_frame,
            columns=columns,
            show="headings",
        )

        y_scroll = ttk.Scrollbar(
            self.tree_frame,
            orient="vertical",
            command=self.tree.yview,
        )

        x_scroll = ttk.Scrollbar(
            self.tree_frame,
            orient="horizontal",
            command=self.tree.xview,
        )

        self.tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=180, anchor="w")

        for row in rows:
            clean_row = []
            for value in row:
                if value is None:
                    clean_row.append("")
                else:
                    clean_row.append(str(value))
            self.tree.insert("", "end", values=clean_row)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        self.tree_frame.grid_rowconfigure(0, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)


if __name__ == "__main__":
    root = tk.Tk()
    app = UsersApp(root)
    root.mainloop()