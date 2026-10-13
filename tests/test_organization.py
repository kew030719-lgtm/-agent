import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.auth import authenticate, connect, hash_password, insert_user


class OrganizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "company.db"
        with closing(connect(self.path)) as db:
            insert_user(db, "admin", "test-password", "admin")
            insert_user(db, "employee", "test-password", "employee")
        self.client = TestClient(create_app(self.path))
        self.client.__enter__()
        self.client.headers["X-Requested-With"] = "employee-assistant"
        self.login("admin")
        self.departments = self.client.get("/api/users/organization").json()["departments"]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def login(self, username: str) -> None:
        response = self.client.post("/api/auth/login", json={"username": username, "password": "test-password"})
        self.assertEqual(response.status_code, 200, response.text)

    def fields(self, level: str = "employee", manager: str | None = None, department: int = 1) -> dict[str, object]:
        return {"display_name": "测试同事", "department_id": department, "job_level": level,
                "manager_username": manager, "role": "employee"}

    def create(self, name: str, level: str, manager: str | None = None) -> dict[str, object]:
        response = self.client.post("/api/users", json={"username": name, "password": "test-password"} | self.fields(level, manager))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_chain_and_chairman_does_not_gain_administration(self) -> None:
        self.create("chair", "chairman")
        self.create("gm", "general_manager", "chair")
        self.create("manager", "department_manager", "gm")
        self.create("lead", "team_lead", "manager")
        self.create("staff", "employee", "lead")
        self.login("chair")
        profile = self.client.get("/api/auth/me").json()
        self.assertEqual(profile["job_level"], "chairman")
        self.assertNotIn("user:create", profile["permissions"])
        self.assertNotIn("kb:finance", profile["permissions"])
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        self.assertEqual(self.client.get("/api/users/organization").status_code, 403)
        self.assertEqual(self.client.post("/api/users/departments", json={"name": "越权部门"}).status_code, 403)
        self.assertEqual(self.client.post("/api/users/chair/profile", json=self.fields() | {"role": "admin"}).status_code, 403)

    def test_invalid_supervisors_and_atomic_rollback(self) -> None:
        self.create("manager", "department_manager")
        self.create("lead", "team_lead", "manager")
        self.create("staff", "employee", "lead")
        before = self.client.get("/api/users").json()
        invalid = [
            ("manager", self.fields("employee", "staff")),  # 循环关系
            ("lead", self.fields("team_lead", "lead")),
            ("staff", self.fields("employee", "lead", 2)),
            ("manager", self.fields("department_manager", None, 2)),  # 影响原部门下属
            ("lead", self.fields("employee", "manager") | {"role": "finance"}),  # 降级影响下属
            ("staff", self.fields("employee", "missing")),
            ("staff", self.fields("employee", None, 9999)),
        ]
        for name, body in invalid:
            response = self.client.post(f"/api/users/{name}/profile", json=body)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.client.get("/api/users").json(), before)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE users SET enabled=0 WHERE username='lead'")
        self.assertEqual(self.client.post("/api/users/staff/profile", json=self.fields("employee", "lead")).status_code, 400)

    def test_edit_updates_existing_session_and_keeps_password(self) -> None:
        employee = TestClient(self.client.app)
        employee.headers["X-Requested-With"] = "employee-assistant"
        self.assertEqual(employee.post("/api/auth/login", json={"username": "employee", "password": "test-password"}).status_code, 200)
        result = self.client.post("/api/users/employee/profile", json=self.fields("general_manager"))
        self.assertEqual(result.status_code, 200, result.text)
        profile = employee.get("/api/auth/me").json()
        self.assertEqual(profile["job_level"], "general_manager")
        self.assertEqual(profile["department_name"], "研发部")
        self.assertNotIn("user:create", profile["permissions"])
        self.login("employee")
        employee.close()

    def test_create_validation_departments_and_self_admin_protection(self) -> None:
        created = self.client.post("/api/users/departments", json={"name": " 客户服务部 "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "客户服务部")
        self.assertEqual(self.client.post("/api/users/departments", json={"name": "客户服务部"}).status_code, 409)
        self.assertEqual(self.client.post("/api/users/departments", json={"name": " "}).status_code, 422)
        for fields, status in ((self.fields("invalid"), 422), (self.fields(manager="missing"), 400), (self.fields(department=999), 400)):
            response = self.client.post("/api/users", json={"username": "invalid", "password": "test-password"} | fields)
            self.assertEqual(response.status_code, status, response.text)
        self.assertFalse(any(row["username"] == "invalid" for row in self.client.get("/api/users").json()))
        self.assertEqual(self.client.post("/api/users/admin/profile", json=self.fields()).status_code, 400)
        self.assertEqual(self.client.post("/api/users/missing/profile", json=self.fields()).status_code, 404)

    def test_legacy_migration_preserves_credentials_and_business_rows(self) -> None:
        path = Path(self.temporary.name) / "old.db"
        salt = b"test-salt"
        password_hash = hash_password("test-password", salt)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users(username TEXT PRIMARY KEY,salt BLOB,password_hash BLOB,role TEXT,enabled INTEGER DEFAULT 1)")
            db.execute("INSERT INTO users VALUES (?,?,?,?,1)", ("legacy", salt, password_hash, "hr"))
            db.execute("CREATE TABLE legacy_business(owner TEXT REFERENCES users(username),amount INTEGER)")
            db.execute("INSERT INTO legacy_business VALUES ('legacy',123)")
        for _ in range(2):
            with closing(connect(path)) as db:
                user = authenticate(db, "legacy", "test-password")
                self.assertIsNotNone(user)
                self.assertEqual(user["role"], "hr")
                self.assertEqual(user["job_level"], "employee")
                self.assertIsNone(user["department_id"])
                self.assertEqual(db.execute("SELECT salt,password_hash FROM users").fetchone(), (salt, password_hash))
                self.assertEqual(db.execute("SELECT * FROM legacy_business").fetchone(), ("legacy", 123))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM departments").fetchone()[0], 8)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
