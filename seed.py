"""Boshlang'ich ma'lumotlar: administrator yaratish va DEMO ma'lumotlar (faqat sinash uchun!)."""
import datetime
import random

from . import seed_data, utils
from .services import attendance, homework, learning, points, schedule, users
from .utils import AppError

DEMO_PASSWORD = "Demo12345"


def create_admin(db, username, password, first_name="Admin", last_name=""):
    return users.create_user(db, username, password, "admin", first_name, last_name)


def seed_questions(db):
    rng = random.Random(2026)
    added = 0
    for item in seed_data.QUESTIONS:
        subject, level, qtype, text, options, explanation = item[:6]
        passage = item[6] if len(item) > 6 else ""
        if db.one("SELECT 1 FROM questions WHERE text=? AND subject_id=(SELECT id FROM subjects WHERE code=?)", (text, subject)):
            continue
        correct = options[0]
        shuffled = options[:]
        rng.shuffle(shuffled)
        sid = db.scalar("SELECT id FROM subjects WHERE code=?", (subject,))
        from .services import questions
        questions.add_question(db, sid, level, qtype, text, shuffled, shuffled.index(correct), explanation, passage)
        added += 1
    for subject, level, word, translation, pron, example in seed_data.WORDS:
        sid = db.scalar("SELECT id FROM subjects WHERE code=?", (subject,))
        if not db.one("SELECT 1 FROM vocab_words WHERE subject_id=? AND word=?", (sid, word)):
            learning.add_word(db, sid, level, word, translation, pron, example)
    return added


def seed_demo(db):
    """Demo foydalanuvchilar, guruhlar, jadval, vazifalar. Qayta chaqirilsa, mavjud bo'lsa o'tkazib yuboradi."""
    if db.one("SELECT 1 FROM users WHERE username='demo_admin'"):
        return False
    seed_questions(db)
    admin_id = users.create_user(db, "demo_admin", DEMO_PASSWORD, "admin", "Demo", "Admin", lang="uz")
    admin = db.one("SELECT * FROM users WHERE id=?", (admin_id,))
    t_ali = users.create_user(db, "demo_ali", DEMO_PASSWORD, "teacher", "Ali", "Valiyev", lang="uz")
    t_olga = users.create_user(db, "demo_olga", DEMO_PASSWORD, "teacher", "Olga", "Petrova", lang="ru")
    names = [("Aziz", "Karimov"), ("Malika", "Rustamova"), ("Jasur", "Aliyev"),
             ("Dilnoza", "Yusupova"), ("Sardor", "Tursunov"), ("Madina", "Hakimova")]
    students = [users.create_user(db, "demo_s%d" % (i + 1), DEMO_PASSWORD, "student", f, l, lang="uz")
                for i, (f, l) in enumerate(names)]
    parent = users.create_user(db, "demo_parent", DEMO_PASSWORD, "parent", "Karim", "Karimov", lang="uz")
    db.execute("INSERT INTO parent_students (parent_id, student_id) VALUES (?,?)", (parent, students[0]))

    def subject(code):
        return db.scalar("SELECT id FROM subjects WHERE code=?", (code,))

    def course(code, name, level, price):
        return db.insert("INSERT INTO courses (subject_id, name, level, price) VALUES (?,?,?,?)", (subject(code), name, level, price))

    c_en1, c_en2 = course("en", "English A1", "A1", 400000), course("en", "English B1", "B1", 500000)
    c_ru1, c_ko1 = course("ru", "Русский A1", "A1", 350000), course("ko", "한국어 A1", "A1", 450000)
    g_en1 = db.insert("INSERT INTO groups (course_id, name, teacher_id, room) VALUES (?,?,?,?)", (c_en1, "English A1 - ertalab", t_ali, "101"))
    g_en2 = db.insert("INSERT INTO groups (course_id, name, teacher_id, room) VALUES (?,?,?,?)", (c_en2, "English B1 - kechki", t_ali, "102"))
    g_ru1 = db.insert("INSERT INTO groups (course_id, name, teacher_id, room) VALUES (?,?,?,?)", (c_ru1, "Русский A1", t_olga, "201"))
    g_ko1 = db.insert("INSERT INTO groups (course_id, name, teacher_id, room) VALUES (?,?,?,?)", (c_ko1, "한국어 A1", t_olga, "202"))
    joined = utils.fmt(utils.now() - datetime.timedelta(days=40))
    for gid, sids in ((g_en1, students[0:3]), (g_en2, [students[3]]), (g_ru1, students[4:6]), (g_ko1, [students[3], students[5]])):
        for sid in sids:
            db.execute("INSERT INTO group_students (group_id, student_id, joined_at) VALUES (?,?,?)", (gid, sid, joined))
    db.execute("INSERT INTO student_levels (student_id, subject_id, level) VALUES (?,?,?)", (students[3], subject("en"), "B1"))

    first = utils.local_today() - datetime.timedelta(days=10)
    for gid, days, hour, room in ((g_en1, [0, 2, 4], "09:00", "101"), (g_en2, [1, 3], "18:00", "102"),
                                  (g_ru1, [1, 3], "15:00", "201"), (g_ko1, [0, 2], "16:30", "202")):
        schedule.create_weekly(db, gid, days, hour, 90, first, 5, room, admin)

    # o'tgan darslarga davomat
    past = db.all("SELECT l.id, l.group_id FROM lessons l WHERE l.starts_at < ? ORDER BY l.id", (utils.fmt(utils.now()),))
    rng = random.Random(7)
    for lesson in past:
        teacher = db.scalar("SELECT teacher_id FROM groups WHERE id=?", (lesson["group_id"],))
        for row in db.all("SELECT student_id FROM group_students WHERE group_id=?", (lesson["group_id"],)):
            status = rng.choices(["present", "late", "absent", "excused"], [8, 1, 1, 1])[0]
            attendance.mark(db, lesson["id"], row["student_id"], status, teacher)

    # uy vazifalari: bittasi muddati o'tgan (jarima mexanizmini ko'rsatish uchun), bittasi kelgusi
    due_past = utils.now() - datetime.timedelta(days=2)
    hw1 = homework.create(db, g_en1, "Unit 3: yangi so'zlar", "10 ta yangi so'zni yozing va gap tuzing", due_past, t_ali)
    homework.submit(db, hw1, students[0], "text", "apple - I eat an apple.")
    homework.create(db, g_en1, "Unit 4: Present Simple", "Mashqlar 1-5", utils.now() + datetime.timedelta(days=3), t_ali)
    homework.create(db, g_ru1, "Упражнение 2", "Прочитайте текст и ответьте на вопросы", utils.now() + datetime.timedelta(days=2), t_olga)

    for name, desc, price, stock in seed_data.SHOP:
        db.insert("INSERT INTO shop_items (name, description, price_coins, stock) VALUES (?,?,?,?)", (name, desc, price, stock))
    for sid in students:
        points.award(db, sid, "xp", rng.randint(20, 120), "teacher_award", "Demo", notify=False)
        points.award(db, sid, "coin", rng.randint(40, 200), "teacher_award", "Demo", notify=False)
    skills = learning.SKILLS
    for sid in students[:3]:
        for skill in skills:
            learning.record_skill(db, sid, subject("en"), skill, rng.randint(55, 95), admin)
    return True
