import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

DB_PATH = os.path.join("instance", "petcare.db")
app.config["DATABASE"] = DB_PATH


# -----------------------------
# DATABASE HELPERS
# -----------------------------
def get_db():
    if "db" not in g:
        os.makedirs("instance", exist_ok=True)
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs("instance", exist_ok=True)

    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row

    with open("schema.sql", "r", encoding="utf-8") as f:
        schema = f.read()

    conn.executescript(schema)
    conn.commit()

    # Seed admin user if missing
    admin_email = "admin@petcare.com"
    existing_admin = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (admin_email,)
    ).fetchone()

    if not existing_admin:
        conn.execute(
            """
            INSERT INTO users
            (name, email, phone, password_hash, role, clinic_name, clinic_location, clinic_license, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "System Admin",
                admin_email,
                "",
                generate_password_hash("admin123"),
                "admin",
                None,
                None,
                None,
                1
            )
        )
        conn.commit()

    conn.close()


# -----------------------------
# ROUTES
# -----------------------------
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    default_role = request.args.get("role", "owner")
    if default_role not in ("owner", "clinic"):
        default_role = "owner"

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        role = request.form.get("role", default_role).strip()

        if role not in ("owner", "clinic"):
            role = "owner"

        clinic_name = request.form.get("clinic_name", "").strip() if role == "clinic" else None
        clinic_location = request.form.get("clinic_location", "").strip() if role == "clinic" else None
        clinic_license = request.form.get("clinic_license", "").strip() if role == "clinic" else None

        if not name or not email or not password or not confirm_password:
            flash("Please fill all required fields.", "danger")
            return render_template("register.html", default_role=role)

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html", default_role=role)

        if role == "clinic":
            if not clinic_name or not clinic_location or not clinic_license:
                flash("Please complete all clinic fields.", "danger")
                return render_template("register.html", default_role=role)

        conn = get_db()
        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_user:
            flash("This email is already registered.", "warning")
            return render_template("register.html", default_role=role)

        conn.execute(
            """
            INSERT INTO users
            (name, email, phone, password_hash, role, clinic_name, clinic_location, clinic_license, is_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                phone,
                generate_password_hash(password),
                role,
                clinic_name,
                clinic_location,
                clinic_license,
                0 if role == "clinic" else 1
            )
        )
        conn.commit()

        if role == "clinic":
            flash("Clinic registration submitted. Please wait for admin approval.", "success")
        else:
            flash("Registration successful. Please log in.", "success")

        return redirect(url_for("login"))

    return render_template("register.html", default_role=default_role)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("Please enter your email and password.", "danger")
            return render_template("login.html")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        if user["role"] == "clinic":
            # 0 = pending, 1 = approved, 2 = rejected
            if user["is_verified"] == 0:
                flash("Your clinic account is pending admin approval.", "warning")
                return redirect(url_for("login"))
            elif user["is_verified"] == 2:
                flash("Your clinic account has been rejected by admin.", "danger")
                return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["role"] = user["role"]

        flash("Login successful!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role")

    if role == "admin":
        return redirect(url_for("admin_dashboard"))
    elif role == "clinic":
        return redirect(url_for("clinic_dashboard"))
    else:
        return redirect(url_for("owner_dashboard"))


@app.route("/owner/dashboard")
def owner_dashboard():
    if "user_id" not in session or session.get("role") != "owner":
        flash("Please log in as a pet owner.", "danger")
        return redirect(url_for("login"))

    return render_template(
        "dashboard.html",
        dashboard_type="Pet Owner Dashboard",
        user_name=session.get("user_name")
    )


@app.route("/clinic/dashboard")
def clinic_dashboard():
    if "user_id" not in session or session.get("role") != "clinic":
        flash("Please log in as a clinic.", "danger")
        return redirect(url_for("login"))

    return render_template(
        "dashboard.html",
        dashboard_type="Clinic Dashboard",
        user_name=session.get("user_name")
    )


@app.route("/admin/dashboard")
def admin_dashboard():
    if "user_id" not in session or session.get("role") != "admin":
        flash("Access denied. Admin only.", "danger")
        return redirect(url_for("login"))

    conn = get_db()

    pending = conn.execute(
        "SELECT * FROM users WHERE role = ? AND is_verified = ? ORDER BY created_at DESC",
        ("clinic", 0)
    ).fetchall()

    approved = conn.execute(
        "SELECT * FROM users WHERE role = ? AND is_verified = ? ORDER BY created_at DESC",
        ("clinic", 1)
    ).fetchall()

    rejected = conn.execute(
        "SELECT * FROM users WHERE role = ? AND is_verified = ? ORDER BY created_at DESC",
        ("clinic", 2)
    ).fetchall()

    total_clinics = conn.execute(
        "SELECT COUNT(*) AS total FROM users WHERE role = ?",
        ("clinic",)
    ).fetchone()["total"]

    total_owners = conn.execute(
        "SELECT COUNT(*) AS total FROM users WHERE role = ?",
        ("owner",)
    ).fetchone()["total"]

    return render_template(
        "admin_dashboard.html",
        pending=pending,
        approved=approved,
        rejected=rejected,
        total_clinics=total_clinics,
        total_owners=total_owners
    )


@app.route("/approve-clinic/<int:user_id>", methods=["POST"])
def approve_clinic(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    conn.execute(
        "UPDATE users SET is_verified = 1 WHERE id = ? AND role = 'clinic'",
        (user_id,)
    )
    conn.commit()

    flash("Clinic approved successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/reject-clinic/<int:user_id>", methods=["POST"])
def reject_clinic(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    conn.execute(
        "UPDATE users SET is_verified = 2 WHERE id = ? AND role = 'clinic'",
        (user_id,)
    )
    conn.commit()

    flash("Clinic rejected.", "warning")
    return redirect(url_for("admin_dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)