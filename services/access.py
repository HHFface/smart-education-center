"""Kim kimning ma'lumotini ko'rishi mumkinligi (rol va bog'lanishlar bo'yicha)."""


def teacher_group_ids(db, teacher_id):
    return [r["id"] for r in db.all("SELECT id FROM groups WHERE teacher_id=?", (teacher_id,))]


def student_group_ids(db, student_id):
    return [r["group_id"] for r in db.all("SELECT group_id FROM group_students WHERE student_id=?", (student_id,))]


def parent_child_ids(db, parent_id):
    return [r["student_id"] for r in db.all("SELECT student_id FROM parent_students WHERE parent_id=?", (parent_id,))]


def teacher_student_ids(db, teacher_id):
    rows = db.all(
        "SELECT DISTINCT gs.student_id FROM group_students gs "
        "JOIN groups g ON g.id = gs.group_id WHERE g.teacher_id=?",
        (teacher_id,),
    )
    return [r["student_id"] for r in rows]


def can_view_student(db, viewer, student_id):
    role = viewer["role"]
    if role == "admin":
        return True
    if role == "student":
        return viewer["id"] == student_id
    if role == "parent":
        return student_id in parent_child_ids(db, viewer["id"])
    if role == "teacher":
        return student_id in teacher_student_ids(db, viewer["id"])
    return False


def can_manage_group(db, viewer, group_id):
    if viewer["role"] == "admin":
        return True
    if viewer["role"] != "teacher":
        return False
    row = db.one("SELECT teacher_id FROM groups WHERE id=?", (group_id,))
    return row is not None and row["teacher_id"] == viewer["id"]


def can_message(db, a, b_id):
    """Ikki foydalanuvchi o'rtasida xabar yozish mumkinmi (a - joriy foydalanuvchi)."""
    if a["id"] == b_id:
        return False
    other = db.one("SELECT id, role FROM users WHERE id=? AND is_active=1", (b_id,))
    if other is None:
        return False
    if a["role"] == "admin" or other["role"] == "admin":
        return True
    pair = {a["role"], other["role"]}
    if pair == {"teacher", "student"}:
        teacher, student = (a["id"], b_id) if a["role"] == "teacher" else (b_id, a["id"])
        return student in teacher_student_ids(db, teacher)
    if pair == {"teacher", "parent"}:
        teacher, parent = (a["id"], b_id) if a["role"] == "teacher" else (b_id, a["id"])
        kids = set(parent_child_ids(db, parent))
        return bool(kids & set(teacher_student_ids(db, teacher)))
    return False


def student_subjects(db, student_id):
    return db.all(
        "SELECT DISTINCT s.* FROM subjects s "
        "JOIN courses c ON c.subject_id = s.id "
        "JOIN groups g ON g.course_id = c.id "
        "JOIN group_students gs ON gs.group_id = g.id "
        "WHERE gs.student_id=? AND s.is_active=1 ORDER BY s.id",
        (student_id,),
    )


def user_name(row):
    if row is None:
        return ""
    full = ("%s %s" % (row["first_name"], row["last_name"])).strip()
    return full or row["username"]
