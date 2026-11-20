"""Rolga qarab navigatsiya: (endpoint, tarjima kaliti, belgi)."""

NAV = {
    "student": [
        ("student.dashboard", "nav.home", "🏠"),
        ("student.schedule", "nav.schedule", "📅"),
        ("student.attendance", "nav.attendance", "✅"),
        ("student.homework", "nav.homework", "📝"),
        ("student.absence", "nav.absence", "🚫"),
        ("student.points", "nav.points", "⭐"),
        ("student.leaderboard", "nav.leaderboard", "🏆"),
        ("battle.hub", "nav.battle", "⚔️"),
        ("student.shop", "nav.shop", "🛍"),
        ("student.results", "nav.results", "📊"),
        ("student.vocabulary", "nav.vocabulary", "📚"),
        ("student.clubs", "nav.clubs", "🎬"),
        ("student.goals", "nav.goals", "🎯"),
        ("student.certificates", "nav.certificates", "🎓"),
        ("student.ai", "nav.ai", "🤖"),
    ],
    "teacher": [
        ("teacher.dashboard", "nav.home", "🏠"),
        ("teacher.schedule", "nav.schedule", "📅"),
        ("teacher.homework_list", "nav.homework", "📝"),
        ("teacher.absences", "nav.absence", "🚫"),
        ("teacher.award", "nav.award", "⭐"),
        ("teacher.exams", "nav.exams", "📊"),
        ("teacher.challenges", "nav.challenges", "⚔️"),
        ("teacher.vocab", "nav.vocabulary", "📚"),
        ("teacher.clubs", "nav.clubs", "🎬"),
        ("teacher.goals", "nav.goals", "🎯"),
    ],
    "parent": [
        ("parent.dashboard", "nav.home", "🏠"),
    ],
    "admin": [
        ("admin.dashboard", "nav.home", "🏠"),
        ("admin.users", "nav.users", "👥"),
        ("admin.invites", "nav.invites", "✉️"),
        ("admin.structure", "nav.structure", "🏫"),
        ("admin.lessons", "nav.schedule", "📅"),
        ("admin.payments", "nav.payments", "💳"),
        ("admin.expenses", "nav.expenses", "🧾"),
        ("admin.questions", "nav.questions", "❓"),
        ("admin.shop", "nav.shop", "🛍"),
        ("admin.battles", "nav.battle", "⚔️"),
        ("admin.absences", "nav.absence", "🚫"),
        ("admin.disputes", "nav.disputes", "⚖️"),
        ("admin.certificates", "nav.certificates", "🎓"),
        ("admin.reports", "nav.reports", "📈"),
        ("admin.settings", "nav.settings", "⚙️"),
        ("admin.audit", "nav.audit", "🗂"),
    ],
}

COMMON = [
    ("account.notifications", "nav.notifications", "🔔"),
    ("account.messages", "nav.messages", "💬"),
    ("account.settings", "nav.account", "👤"),
]


def for_role(role):
    return NAV.get(role, []), COMMON
