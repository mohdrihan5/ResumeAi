RAZORPAY_KEY_ID = "YOUR_KEY_ID"
RAZORPAY_KEY_SECRET = "YOUR_KEY_SECRET"
from flask import (
    Flask, request, redirect, url_for,
    session, render_template_string, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta
import sqlite3
import os
import re
import json
import hmac
import hashlib

# ============================================================
# OPTIONAL RAZORPAY
# ============================================================

try:
    import  razorpay
except ImportError:
    razorpay = None


# ============================================================
# APP CONFIG
# ============================================================

app = Flask(__name__)

app.secret_key = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"

DB = "resumeai_pro.db"
UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# ============================================================
# RAZORPAY CONFIG
# ============================================================
# Put your Razorpay TEST keys here first.

RAZORPAY_KEY_ID = "YOUR_KEY_ID"
RAZORPAY_KEY_SECRET = "YOUR_KEY_SECRET"

if (
    razorpay
    and RAZORPAY_KEY_ID != "YOUR_KEY_ID"
    and RAZORPAY_KEY_SECRET != "YOUR_KEY_SECRET"
):
    razorpay_client = razorpay.Client(
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    )
else:
    razorpay_client = None


# ============================================================
# PLANS
# ============================================================

PLANS = {
    "pro": {
        "name": "Pro",
        "price": 199,
        "days": 30,
        "scans": 30,
        "description": "30 ATS scans for 30 days"
    },

    "premium": {
        "name": "Premium",
        "price": 499,
        "days": 30,
        "scans": 100,
        "description": "100 ATS scans for 30 days"
    }
}


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(DB)

    conn.row_factory = sqlite3.Row

    return conn


def current_time():

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            plan TEXT DEFAULT 'free',
            plan_expiry TEXT,
            scans_left INTEGER DEFAULT 3,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_name TEXT NOT NULL,
            job_title TEXT,
            ats_score INTEGER,
            matched_skills TEXT,
            missing_skills TEXT,
            keywords TEXT,
            suggestions TEXT,
            word_count INTEGER,
            scanned_at TEXT NOT NULL,
            FOREIGN KEY(user_id)
            REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan TEXT NOT NULL,
            amount INTEGER NOT NULL,
            razorpay_order_id TEXT,
            razorpay_payment_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id)
            REFERENCES users(id)
        )
    """)

    admin = conn.execute("""
        SELECT id FROM users
        WHERE email=?
    """, (
        "admin@resumeai.com",
    )).fetchone()

    if not admin:

        conn.execute("""
            INSERT INTO users
            (name,email,password,role,plan,scans_left,created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            "Administrator",
            "admin@resumeai.com",
            generate_password_hash("admin123"),
            "admin",
            "premium",
            999999,
            current_time()
        ))

    conn.commit()

    conn.close()


# ============================================================
# AUTH
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return redirect(
                url_for("login")
            )

        return function(*args, **kwargs)

    return wrapper


def admin_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return redirect(
                url_for("login")
            )

        if session.get("role") != "admin":

            flash(
                "Admin access required.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )

        return function(*args, **kwargs)

    return wrapper


# ============================================================
# SKILLS
# ============================================================

SKILLS = [
    "Python",
    "Java",
    "C",
    "C++",
    "C#",
    "JavaScript",
    "TypeScript",
    "HTML",
    "CSS",
    "React",
    "Angular",
    "Node.js",
    "Flask",
    "Django",
    "PHP",
    "SQL",
    "MySQL",
    "PostgreSQL",
    "MongoDB",
    "SQLite",
    "SQL Server",
    "Oracle",
    "Excel",
    "Power BI",
    "Tableau",
    "Pandas",
    "NumPy",
    "Matplotlib",
    "Seaborn",
    "Machine Learning",
    "Deep Learning",
    "Artificial Intelligence",
    "AI",
    "NLP",
    "Natural Language Processing",
    "TensorFlow",
    "PyTorch",
    "Scikit-learn",
    "Data Analysis",
    "Data Analytics",
    "Data Science",
    "Data Visualization",
    "Statistics",
    "Git",
    "GitHub",
    "Docker",
    "AWS",
    "Azure",
    "Linux",
    "REST API",
    "API",
    "Communication",
    "Leadership",
    "Problem Solving",
    "Teamwork",
    "Time Management",
    "PowerPoint",
    "MS Office"
]


STOPWORDS = {
    "the", "and", "for", "with", "that",
    "this", "are", "you", "your", "our",
    "from", "have", "will", "job", "role",
    "work", "years", "experience", "required",
    "candidate", "looking", "should", "must",
    "using", "into", "about", "their",
    "they", "who", "can", "has", "been",
    "being", "also", "such", "than",
    "more", "other", "what", "which",
    "we", "to", "in", "of", "on",
    "a", "an", "is", "as", "at", "or",
    "by"
}


def normalize(text):

    text = text.lower()

    text = text.replace(
        "c++",
        " cpp "
    )

    text = text.replace(
        "c#",
        " csharp "
    )

    text = re.sub(
        r"[^a-z0-9+#.\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def detect_skills(text):

    text = normalize(text)

    found = []

    for skill in SKILLS:

        skill_n = normalize(skill)

        if re.search(
            r"(?<![a-z0-9])"
            + re.escape(skill_n)
            + r"(?![a-z0-9])",
            text
        ):

            found.append(skill)

    return sorted(
        list(set(found)),
        key=str.lower
    )


def extract_keywords(text):

    words = re.findall(
        r"\b[a-z][a-z0-9+#.-]{2,}\b",
        normalize(text)
    )

    result = []

    for word in words:

        if word in STOPWORDS:
            continue

        if word not in result:
            result.append(word)

    return result[:40]


# ============================================================
# FILE TEXT EXTRACTION
# ============================================================

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(
            ".",
            1
        )[1].lower()
        in {"pdf", "docx", "txt"}
    )


def extract_text(path):

    extension = os.path.splitext(
        path
    )[1].lower()

    try:

        if extension == ".txt":

            with open(
                path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as file:

                return file.read()


        if extension == ".pdf":

            from PyPDF2 import PdfReader

            reader = PdfReader(path)

            text = ""

            for page in reader.pages:

                page_text = page.extract_text()

                if page_text:
                    text += page_text + "\n"

            return text


        if extension == ".docx":

            from docx import Document

            document = Document(path)

            return "\n".join(
                p.text
                for p in document.paragraphs
            )

    except Exception as error:

        print(
            "Text extraction error:",
            error
        )

    return ""


# ============================================================
# ATS ENGINE
# ============================================================

def analyze_resume(resume, job_description):

    resume_n = normalize(resume)

    jd_n = normalize(job_description)

    resume_skills = detect_skills(resume)

    jd_skills = detect_skills(
        job_description
    )

    resume_set = {
        x.lower()
        for x in resume_skills
    }

    matched = [
        x
        for x in jd_skills
        if x.lower() in resume_set
    ]

    missing = [
        x
        for x in jd_skills
        if x.lower() not in resume_set
    ]


    # ------------------------------
    # SKILLS 50
    # ------------------------------

    if jd_skills:

        skill_score = (
            len(matched)
            / len(jd_skills)
        ) * 50

    else:

        skill_score = 25


    # ------------------------------
    # KEYWORDS 20
    # ------------------------------

    keywords = extract_keywords(
        job_description
    )

    keyword_matches = [
        x
        for x in keywords
        if x in resume_n
    ]

    if keywords:

        keyword_score = (
            len(keyword_matches)
            / len(keywords)
        ) * 20

    else:

        keyword_score = 10


    # ------------------------------
    # STRUCTURE 15
    # ------------------------------

    sections = [
        "summary",
        "objective",
        "experience",
        "education",
        "skills",
        "projects",
        "certification",
        "achievements"
    ]

    section_count = sum(
        1
        for section in sections
        if section in resume_n
    )

    structure_score = (
        section_count
        / len(sections)
    ) * 15


    # ------------------------------
    # LENGTH 15
    # ------------------------------

    word_count = len(
        resume.split()
    )

    if 400 <= word_count <= 900:

        length_score = 15

    elif 250 <= word_count < 400:

        length_score = 12

    elif 900 < word_count <= 1200:

        length_score = 12

    elif 150 <= word_count < 250:

        length_score = 8

    else:

        length_score = 4


    score = round(
        skill_score
        + keyword_score
        + structure_score
        + length_score
    )

    score = max(
        0,
        min(score, 100)
    )


    suggestions = []

    if missing:

        suggestions.append(
            "Add relevant missing skills: "
            + ", ".join(missing[:8])
        )

    if word_count < 250:

        suggestions.append(
            "Your resume is too short. "
            "Add projects, achievements and experience."
        )

    if word_count > 1200:

        suggestions.append(
            "Your resume is too long. "
            "Remove irrelevant information."
        )

    if "projects" not in resume_n:

        suggestions.append(
            "Add a Projects section with technologies "
            "and measurable results."
        )

    if "experience" not in resume_n:

        suggestions.append(
            "Add internship, freelance or practical "
            "experience relevant to the target role."
        )

    if (
        "summary" not in resume_n
        and
        "objective" not in resume_n
    ):

        suggestions.append(
            "Add a professional summary at the top."
        )

    if not keyword_matches:

        suggestions.append(
            "Use important job-description keywords "
            "naturally inside your resume."
        )

    if not suggestions:

        suggestions.append(
            "Your resume has strong alignment. "
            "Keep achievements measurable."
        )


    return {
        "score": score,
        "resume_skills": resume_skills,
        "matched": matched,
        "missing": missing,
        "keywords": keywords,
        "keyword_matches": keyword_matches,
        "suggestions": suggestions,
        "word_count": word_count
    }


# ============================================================
# COMMON CSS
# ============================================================

STYLE = r"""
<style>

*{
box-sizing:border-box;
margin:0;
padding:0
}

:root{
--bg:#f6f7fb;
--card:#fff;
--text:#111827;
--muted:#7c8497;
--line:#e8eaf1;
--primary:#635bff;
--purple:#8b5cf6;
--green:#10b981;
--red:#ef4444;
--orange:#f59e0b;
--shadow:0 20px 60px rgba(20,25,60,.07)
}

body{
font-family:Inter,Arial,sans-serif;
background:
radial-gradient(circle at 5% 0%,#e9e7ff,transparent 25%),
radial-gradient(circle at 100% 0%,#f0e6ff,transparent 25%),
var(--bg);
color:var(--text);
min-height:100vh
}

a{
text-decoration:none;
color:inherit
}

button,input,textarea{
font:inherit
}

.nav{
height:72px;
display:flex;
align-items:center;
justify-content:space-between;
padding:0 5%;
background:rgba(255,255,255,.88);
backdrop-filter:blur(20px);
border-bottom:1px solid var(--line);
position:sticky;
top:0;
z-index:99
}

.brand{
display:flex;
align-items:center;
gap:10px;
font-size:20px;
font-weight:900
}

.logo{
width:39px;
height:39px;
display:grid;
place-items:center;
border-radius:12px;
color:white;
background:linear-gradient(135deg,#635bff,#8b5cf6);
box-shadow:0 10px 25px #635bff40
}

.nav-actions{
display:flex;
align-items:center;
gap:10px
}

.nav-link{
font-size:12px;
font-weight:800;
color:#5f6879;
padding:10px 13px
}

.avatar{
width:35px;
height:35px;
border-radius:50%;
display:grid;
place-items:center;
color:white;
font-size:12px;
font-weight:900;
background:linear-gradient(135deg,#6366f1,#a855f7)
}

.container{
width:92%;
max-width:1200px;
margin:auto
}

.page{
padding:40px 0 80px
}

.btn{
border:0;
border-radius:12px;
padding:12px 17px;
font-size:12px;
font-weight:900;
cursor:pointer;
display:inline-flex;
align-items:center;
justify-content:center;
gap:8px
}

.btn-primary{
color:white;
background:linear-gradient(100deg,#5148e8,#7c3aed);
box-shadow:0 10px 25px #635bff25
}

.btn-light{
background:#f0f1f6;
color:#525b6b
}

.btn-danger{
background:#fff0f1;
color:#dc2626
}

.card{
background:white;
border:1px solid var(--line);
border-radius:23px;
box-shadow:var(--shadow)
}

.flash{
padding:13px 16px;
border-radius:12px;
background:#fff1f2;
border:1px solid #fecdd3;
color:#be123c;
font-size:12px;
font-weight:700;
margin-bottom:18px
}

.flash.success{
background:#ecfdf5;
border-color:#a7f3d0;
color:#047857
}

/* AUTH */

.auth{
min-height:calc(100vh - 72px);
display:grid;
place-items:center;
padding:30px
}

.auth-card{
width:100%;
max-width:430px;
padding:34px
}

.auth-brand{
justify-content:center;
margin-bottom:25px
}

.auth-title{
text-align:center;
font-size:29px;
font-weight:900
}

.auth-sub{
text-align:center;
font-size:12px;
color:var(--muted);
margin:8px 0 25px
}

.label{
font-size:11px;
font-weight:800;
color:#596275;
display:block;
margin-bottom:7px
}

.input{
width:100%;
border:1px solid var(--line);
border-radius:12px;
padding:13px;
outline:none;
margin-bottom:15px
}

.input:focus,
textarea:focus{
border-color:var(--primary);
box-shadow:0 0 0 4px #635bff12
}

.auth-bottom{
text-align:center;
font-size:12px;
color:var(--muted);
margin-top:18px
}

.auth-bottom a{
color:var(--primary);
font-weight:800
}

/* DASHBOARD */

.topbar{
display:flex;
align-items:center;
justify-content:space-between;
margin-bottom:25px
}

.topbar h1{
font-size:31px;
letter-spacing:-1px
}

.topbar p{
font-size:12px;
color:var(--muted);
margin-top:5px
}

.scanner{
padding:24px
}

.grid2{
display:grid;
grid-template-columns:1fr 1.25fr;
gap:18px
}

.panel{
background:#fafbff;
border:1px solid var(--line);
border-radius:18px;
padding:20px
}

.panel h3{
font-size:15px;
margin-bottom:15px
}

.dropzone{
height:280px;
border:2px dashed #ccd1df;
border-radius:17px;
background:white;
display:flex;
flex-direction:column;
justify-content:center;
align-items:center;
text-align:center;
cursor:pointer;
transition:.2s
}

.dropzone:hover{
border-color:var(--primary);
background:#faf9ff
}

.upload{
width:60px;
height:60px;
display:grid;
place-items:center;
border-radius:18px;
background:#eeedff;
color:#635bff;
font-size:28px;
margin-bottom:15px
}

.dropzone strong{
font-size:14px
}

.dropzone span{
font-size:11px;
color:var(--muted);
margin-top:7px
}

.file-name{
font-size:11px;
color:var(--primary);
font-weight:800;
margin-top:12px
}

textarea{
width:100%;
height:280px;
border:1px solid var(--line);
border-radius:14px;
padding:14px;
outline:none;
resize:vertical;
font-size:12px;
line-height:1.7
}

.full{
width:100%;
margin-top:18px
}

/* PLAN */

.plan-banner{
margin-bottom:20px;
padding:20px;
display:flex;
align-items:center;
justify-content:space-between;
background:linear-gradient(135deg,#11152a,#292d4c);
color:white;
border-radius:20px
}

.plan-name{
font-size:20px;
font-weight:900
}

.plan-info{
font-size:11px;
color:#bfc4d6;
margin-top:5px
}

/* SCORE */

.score{
margin-top:18px;
padding:27px;
border-radius:24px;
color:white;
background:
radial-gradient(circle at 10% 10%,#8b5cf640,transparent 30%),
linear-gradient(135deg,#11152a,#242947);
display:grid;
grid-template-columns:200px 1fr;
gap:30px;
align-items:center
}

.circle{
width:170px;
height:170px;
border-radius:50%;
margin:auto;
display:grid;
place-items:center;
background:conic-gradient(
#8b5cf6 var(--score),
#ffffff15 0
);
position:relative
}

.circle:after{
content:"";
position:absolute;
width:130px;
height:130px;
border-radius:50%;
background:#171c33
}

.circle-value{
z-index:2;
font-size:38px;
font-weight:900
}

.score h2{
font-size:26px;
margin-bottom:9px
}

.score p{
font-size:12px;
line-height:1.8;
color:#bcc2d5
}

.status{
display:inline-block;
padding:8px 12px;
border-radius:20px;
background:#8b5cf630;
color:#dcd2ff;
font-size:10px;
font-weight:900;
margin-top:13px
}

.stats{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:13px;
margin-top:15px
}

.stat{
padding:18px
}

.stat-num{
font-size:25px;
font-weight:900;
margin-top:7px
}

.stat-label{
font-size:9px;
font-weight:800;
color:var(--muted)
}

.result-grid{
display:grid;
grid-template-columns:1fr 1fr;
gap:14px;
margin-top:15px
}

.result-box{
padding:21px
}

.result-box h3{
font-size:14px;
margin-bottom:12px
}

.chip{
display:inline-flex;
padding:7px 10px;
border-radius:20px;
font-size:9px;
font-weight:800;
margin:3px
}

.green{
background:#ecfdf5;
color:#047857
}

.red{
background:#fff1f2;
color:#be123c
}

.blue{
background:#eef2ff;
color:#4f46e5
}

.suggestions{
padding:21px;
margin-top:15px;
border-radius:19px;
background:linear-gradient(135deg,#eef2ff,#faf5ff);
border:1px solid #ddd6fe
}

.suggestion{
background:white;
padding:11px;
border-radius:10px;
font-size:11px;
color:#4b5563;
margin-top:7px
}

/* TABLE */

.history{
margin-top:20px;
overflow:hidden
}

.history-head{
padding:20px;
display:flex;
justify-content:space-between;
align-items:center;
border-bottom:1px solid var(--line)
}

.table-wrap{
overflow-x:auto
}

table{
width:100%;
border-collapse:collapse;
min-width:700px
}

th,td{
padding:13px 18px;
text-align:left;
border-bottom:1px solid var(--line);
font-size:11px
}

th{
font-size:9px;
color:#8992a4;
text-transform:uppercase
}

.score-pill{
display:inline-block;
padding:6px 9px;
border-radius:20px;
background:#eef2ff;
color:#4f46e5;
font-weight:900
}

/* PRICING */

.pricing{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:18px;
margin-top:20px
}

.price-card{
padding:25px;
position:relative
}

.price-card.featured{
border:2px solid #635bff
}

.price-name{
font-size:15px;
font-weight:900
}

.price{
font-size:38px;
font-weight:900;
margin:15px 0
}

.price small{
font-size:11px;
color:var(--muted);
font-weight:500
}

.price-desc{
font-size:11px;
color:var(--muted);
line-height:1.7;
margin-bottom:18px
}

.feature{
font-size:11px;
padding:7px 0
}

/* ADMIN */

.admin-stats{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:15px
}

.admin-stat{
padding:21px
}

.admin-label{
font-size:9px;
font-weight:900;
color:var(--muted)
}

.admin-number{
font-size:30px;
font-weight:900;
margin-top:8px
}

/* MOBILE */

@media(max-width:850px){

.grid2,
.score,
.result-grid{
grid-template-columns:1fr
}

.stats,
.admin-stats{
grid-template-columns:1fr 1fr
}

.pricing{
grid-template-columns:1fr
}

}

@media(max-width:550px){

.stats,
.admin-stats{
grid-template-columns:1fr
}

.nav-link{
display:none
}

.topbar{
align-items:flex-start;
gap:10px
}

.topbar h1{
font-size:24px
}

.scanner{
padding:13px
}

.panel{
padding:14px
}

.auth-card{
padding:25px
}

}

</style>
"""


# ============================================================
# NAV
# ============================================================

NAV = r"""
<nav class="nav">

<a class="brand" href="/">
<div class="logo">✦</div>
<span>Resume<span style="color:#635bff">AI</span></span>
</a>

<div class="nav-actions">

{% if session.get("user_id") %}

<div class="avatar">
{{ session.get("name","U")[0]|upper }}
</div>

{% if session.get("role") == "admin" %}

<a class="nav-link" href="/admin">
Admin
</a>

{% else %}

<a class="nav-link" href="/dashboard">
Dashboard
</a>

<a class="nav-link" href="/pricing">
Plans
</a>

{% endif %}

<a class="nav-link" href="/logout">
Logout
</a>

{% else %}

<a class="nav-link" href="/login">
Login
</a>

<a class="btn btn-primary" href="/signup">
Get Started
</a>

{% endif %}

</div>

</nav>
"""


# ============================================================
# LOGIN
# ============================================================

LOGIN_HTML = STYLE + NAV + r"""

<div class="auth">

<div class="card auth-card">

<div class="brand auth-brand">
<div class="logo">✦</div>
<span>Resume<span style="color:#635bff">AI</span></span>
</div>

<div class="auth-title">
Welcome back
</div>

<div class="auth-sub">
Sign in to your ATS intelligence dashboard.
</div>

{% with messages=get_flashed_messages(with_categories=true) %}

{% for category,message in messages %}

<div class="flash
{% if category == 'success' %}
success
{% endif %}">
{{ message }}
</div>

{% endfor %}

{% endwith %}

<form method="POST">

<label class="label">
Email
</label>

<input
class="input"
type="email"
name="email"
required
placeholder="you@example.com"
>

<label class="label">
Password
</label>

<input
class="input"
type="password"
name="password"
required
placeholder="••••••••"
>

<button class="btn btn-primary full">
Sign In →
</button>

</form>

<div class="auth-bottom">
Don't have an account?
<a href="/signup">Create account</a>
</div>

</div>

</div>
"""


@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE email=?
        """, (
            email,
        )).fetchone()

        conn.close()

        if (
            user
            and
            check_password_hash(
                user["password"],
                password
            )
        ):

            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["email"] = user["email"]
            session["role"] = user["role"]

            if user["role"] == "admin":

                return redirect(
                    url_for("admin")
                )

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid email or password.",
            "error"
        )

    return render_template_string(
        LOGIN_HTML
    )


# ============================================================
# SIGNUP
# ============================================================

SIGNUP_HTML = STYLE + NAV + r"""

<div class="auth">

<div class="card auth-card">

<div class="brand auth-brand">
<div class="logo">✦</div>
<span>Resume<span style="color:#635bff">AI</span></span>
</div>

<div class="auth-title">
Create account
</div>

<div class="auth-sub">
Start analyzing resumes with ATS intelligence.
</div>

{% with messages=get_flashed_messages(with_categories=true) %}

{% for category,message in messages %}

<div class="flash">
{{ message }}
</div>

{% endfor %}

{% endwith %}

<form method="POST">

<label class="label">
Full Name
</label>

<input
class="input"
name="name"
required
placeholder="Your name"
>

<label class="label">
Email
</label>

<input
class="input"
type="email"
name="email"
required
placeholder="you@example.com"
>

<label class="label">
Password
</label>

<input
class="input"
type="password"
name="password"
required
placeholder="Minimum 6 characters"
>

<button class="btn btn-primary full">
Create Account →
</button>

</form>

<div class="auth-bottom">
Already have an account?
<a href="/login">Sign in</a>
</div>

</div>

</div>
"""


@app.route(
    "/signup",
    methods=["GET", "POST"]
)
def signup():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("signup")
            )

        conn = get_db()

        try:

            conn.execute("""
                INSERT INTO users
                (name,email,password,role,plan,
                 scans_left,created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                name,
                email,
                generate_password_hash(password),
                "user",
                "free",
                3,
                current_time()
            ))

            conn.commit()

            flash(
                "Account created successfully.",
                "success"
            )

            return redirect(
                url_for("login")
            )

        except sqlite3.IntegrityError:

            flash(
                "Email already registered.",
                "error"
            )

        finally:

            conn.close()

    return render_template_string(
        SIGNUP_HTML
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# USER INFO
# ============================================================

def get_current_user():

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id=?
    """, (
        session["user_id"],
    )).fetchone()

    conn.close()

    return user


# ============================================================
# DASHBOARD
# ============================================================

DASHBOARD_HTML = STYLE + NAV + r"""

<div class="container page">

<div class="topbar">

<div>

<h1>
Hello, {{ user.name }} 👋
</h1>

<p>
Analyze your resume against any target job.
</p>

</div>

<a class="btn btn-primary"
href="/pricing">
Upgrade Plan
</a>

</div>


{% with messages=get_flashed_messages(with_categories=true) %}

{% for category,message in messages %}

<div class="flash
{% if category == 'success' %}
success
{% endif %}">
{{ message }}
</div>

{% endfor %}

{% endwith %}


<div class="plan-banner">

<div>

<div class="plan-name">
{{ user.plan|upper }} PLAN
</div>

<div class="plan-info">

{% if user.plan == "free" %}

{{ user.scans_left }} free scans remaining

{% else %}

{{ user.scans_left }} scans remaining

{% if user.plan_expiry %}
• Expires {{ user.plan_expiry }}
{% endif %}

{% endif %}

</div>

</div>

<a
href="/pricing"
class="btn btn-light">

{% if user.plan == "free" %}
Upgrade
{% else %}
Manage Plan
{% endif %}

</a>

</div>


<div class="card scanner">

<form
method="POST"
action="/scan"
enctype="multipart/form-data">

<div class="grid2">


<div class="panel">

<h3>
📄 Upload Resume
</h3>

<label
class="dropzone"
for="resume">

<div class="upload">
↑
</div>

<strong>
Drop your resume here
</strong>

<span>
PDF, DOCX or TXT • Max 10MB
</span>

<div
class="file-name"
id="fileName">
No file selected
</div>

<input
id="resume"
name="resume"
type="file"
accept=".pdf,.docx,.txt"
required
style="display:none"
>

</label>

</div>


<div class="panel">

<h3>
🎯 Target Job
</h3>

<label class="label">
Job Title
</label>

<input
class="input"
name="job_title"
placeholder="e.g. Junior Data Analyst"
>

<label class="label">
Job Description
</label>

<textarea
name="job_description"
placeholder="Paste the complete job description here..."
required
></textarea>

</div>

</div>


<button
class="btn btn-primary full">

✦ Analyze Resume

</button>

</form>

</div>


{% if result %}

<div
class="score"
style="--score:{{ result.score }}%;">

<div class="circle">

<div class="circle-value">
{{ result.score }}
</div>

</div>

<div>

<h2>
ATS Compatibility Score
</h2>

<p>
Your resume was analyzed for skills,
keywords, structure and content alignment.
</p>

<span class="status">

{% if result.score >= 80 %}
🔥 Excellent Match
{% elif result.score >= 60 %}
👍 Good Match
{% elif result.score >= 40 %}
⚠️ Needs Improvement
{% else %}
🚨 Low Match
{% endif %}

</span>

</div>

</div>


<div class="stats">

<div class="card stat">

<div>🎯</div>

<div class="stat-num">
{{ result.score }}%
</div>

<div class="stat-label">
ATS SCORE
</div>

</div>

<div class="card stat">

<div>✓</div>

<div class="stat-num">
{{ result.matched|length }}
</div>

<div class="stat-label">
MATCHED SKILLS
</div>

</div>

<div class="card stat">

<div>!</div>

<div class="stat-num">
{{ result.missing|length }}
</div>

<div class="stat-label">
MISSING SKILLS
</div>

</div>

<div class="card stat">

<div>📝</div>

<div class="stat-num">
{{ result.word_count }}
</div>

<div class="stat-label">
WORD COUNT
</div>

</div>

</div>


<div class="result-grid">


<div class="card result-box">

<h3>
✓ Matched Skills
</h3>

{% for skill in result.matched %}

<span class="chip green">
{{ skill }}
</span>

{% else %}

<p style="color:#8992a4;font-size:11px">
No matched skills found.
</p>

{% endfor %}

</div>


<div class="card result-box">

<h3>
! Missing Skills
</h3>

{% for skill in result.missing %}

<span class="chip red">
{{ skill }}
</span>

{% else %}

<p style="color:#8992a4;font-size:11px">
No major missing skills.
</p>

{% endfor %}

</div>


<div class="card result-box">

<h3>
🔑 Job Keywords
</h3>

{% for keyword in result.keywords[:20] %}

<span class="chip blue">
{{ keyword }}
</span>

{% endfor %}

</div>


<div class="card result-box">

<h3>
✓ Keyword Matches
</h3>

{% for keyword in result.keyword_matches %}

<span class="chip green">
{{ keyword }}
</span>

{% endfor %}

</div>

</div>


<div class="suggestions">

<h3>
💡 Resume Intelligence
</h3>

{% for suggestion in result.suggestions %}

<div class="suggestion">
→ {{ suggestion }}
</div>

{% endfor %}

</div>

{% endif %}


<div class="card history">

<div class="history-head">

<h3>
Recent Scans
</h3>

<span style="font-size:10px;color:#8992a4">
Latest analyses
</span>

</div>

<div class="table-wrap">

<table>

<thead>

<tr>
<th>Resume</th>
<th>Job</th>
<th>Score</th>
<th>Words</th>
<th>Date</th>
</tr>

</thead>

<tbody>

{% for scan in history %}

<tr>

<td>
{{ scan.resume_name }}
</td>

<td>
{{ scan.job_title or "General" }}
</td>

<td>
<span class="score-pill">
{{ scan.ats_score }}%
</span>
</td>

<td>
{{ scan.word_count }}
</td>

<td>
{{ scan.scanned_at }}
</td>

</tr>

{% else %}

<tr>
<td colspan="5">
No scans yet.
</td>
</tr>

{% endfor %}

</tbody>

</table>

</div>

</div>

</div>


<script>

const input =
document.getElementById("resume");

const fileName =
document.getElementById("fileName");

input.addEventListener(
"change",
function(){

if(this.files.length){

fileName.textContent =
"✓ " + this.files[0].name;

}

});

</script>
"""


@app.route("/dashboard")
@login_required
def dashboard():

    user = get_current_user()

    if user["role"] == "admin":

        return redirect(
            url_for("admin")
        )

    conn = get_db()

    history = conn.execute("""
        SELECT *
        FROM scans
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 20
    """, (
        user["id"],
    )).fetchall()

    conn.close()

    return render_template_string(
        DASHBOARD_HTML,
        user=user,
        history=history,
        result=None
    )


# ============================================================
# SCAN
# ============================================================

@app.route(
    "/scan",
    methods=["POST"]
)
@login_required
def scan():

    user = get_current_user()

    if user["role"] != "admin":

        if user["scans_left"] <= 0:

            flash(
                "You have no scans left. Please upgrade your plan.",
                "error"
            )

            return redirect(
                url_for("pricing")
            )


    resume = request.files.get(
        "resume"
    )

    job_title = request.form.get(
        "job_title",
        ""
    ).strip()

    job_description = request.form.get(
        "job_description",
        ""
    ).strip()


    if not resume or not resume.filename:

        flash(
            "Please upload a resume.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    if not allowed_file(
        resume.filename
    ):

        flash(
            "Only PDF, DOCX and TXT files are supported.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    if not job_description:

        flash(
            "Please enter the job description.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    filename = secure_filename(
        resume.filename
    )

    unique_name = (
        datetime.now().strftime(
            "%Y%m%d%H%M%S"
        )
        + "_"
        + filename
    )

    path = os.path.join(
        UPLOAD_DIR,
        unique_name
    )

    resume.save(path)

    text = extract_text(path)


    if not text.strip():

        flash(
            "Could not extract text from the resume.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


    result = analyze_resume(
        text,
        job_description
    )


    conn = get_db()

    conn.execute("""
        INSERT INTO scans
        (
            user_id,
            resume_name,
            job_title,
            ats_score,
            matched_skills,
            missing_skills,
            keywords,
            suggestions,
            word_count,
            scanned_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (

        user["id"],
        filename,
        job_title,
        result["score"],
        json.dumps(
            result["matched"]
        ),
        json.dumps(
            result["missing"]
        ),
        json.dumps(
            result["keywords"]
        ),
        json.dumps(
            result["suggestions"]
        ),
        result["word_count"],
        current_time()

    ))


    if user["role"] != "admin":

        conn.execute("""
            UPDATE users
            SET scans_left=scans_left-1
            WHERE id=?
            AND scans_left>0
        """, (
            user["id"],
        ))


    conn.commit()


    history = conn.execute("""
        SELECT *
        FROM scans
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 20
    """, (
        user["id"],
    )).fetchall()

    updated_user = conn.execute("""
        SELECT *
        FROM users
        WHERE id=?
    """, (
        user["id"],
    )).fetchone()

    conn.close()


    return render_template_string(
        DASHBOARD_HTML,
        user=updated_user,
        history=history,
        result=result
    )


# ============================================================
# PRICING
# ============================================================

PRICING_HTML = STYLE + NAV + r"""

<div class="container page">

<div style="text-align:center">

<div style="
display:inline-block;
padding:7px 12px;
background:#eeedff;
color:#584fe8;
border-radius:30px;
font-size:10px;
font-weight:900">
RESUMEAI PRO
</div>

<h1 style="
font-size:42px;
margin-top:15px;
letter-spacing:-2px">
Choose your plan
</h1>

<p style="
color:#8992a4;
font-size:12px;
margin-top:8px">
Unlock more ATS resume scans.
</p>

</div>


<div class="pricing">


<div class="card price-card">

<div class="price-name">
Free
</div>

<div class="price">
₹0
</div>

<div class="price-desc">
For trying ResumeAI.
</div>

<div class="feature">
✓ 3 ATS scans
</div>

<div class="feature">
✓ Skill matching
</div>

<div class="feature">
✓ Keyword analysis
</div>

<div class="feature">
✓ Resume suggestions
</div>

<br>

<a
href="/dashboard"
class="btn btn-light"
style="width:100%">

Current Plan

</a>

</div>


<div class="card price-card featured">

<div style="
position:absolute;
top:-11px;
right:20px;
padding:5px 9px;
background:#635bff;
color:white;
border-radius:20px;
font-size:9px;
font-weight:900">
POPULAR
</div>

<div class="price-name">
Pro
</div>

<div class="price">
₹199
<small>/month</small>
</div>

<div class="price-desc">
For active job seekers.
</div>

<div class="feature">
✓ 30 ATS scans
</div>

<div class="feature">
✓ Advanced skill matching
</div>

<div class="feature">
✓ Keyword analysis
</div>

<div class="feature">
✓ Resume recommendations
</div>

<br>

<button
class="btn btn-primary"
style="width:100%"
onclick="pay('pro')">

Upgrade to Pro

</button>

</div>


<div class="card price-card">

<div class="price-name">
Premium
</div>

<div class="price">
₹499
<small>/month</small>
</div>

<div class="price-desc">
For serious job seekers.
</div>

<div class="feature">
✓ 100 ATS scans
</div>

<div class="feature">
✓ Advanced analysis
</div>

<div class="feature">
✓ Keyword matching
</div>

<div class="feature">
✓ Unlimited resume improvements
</div>

<br>

<button
class="btn btn-primary"
style="width:100%"
onclick="pay('premium')">

Upgrade to Premium

</button>

</div>

</div>

</div>


<script src="https://checkout.razorpay.com/v1/checkout.js"></script>

<script>

async function pay(plan){

const response =
await fetch(
"/create_order/" + plan,
{
method:"POST"
}
);

const data =
await response.json();

if(!data.success){

alert(data.message);

return;

}

const options = {

key:data.key,

amount:data.amount,

currency:"INR",

name:"ResumeAI",

description:data.description,

order_id:data.order_id,

handler:function(response){

fetch(
"/payment_success",
{
method:"POST",
headers:{
"Content-Type":
"application/json"
},
body:JSON.stringify(response)
}
)
.then(r => r.json())
.then(result => {

if(result.success){

window.location.href =
"/dashboard";

}else{

alert(result.message);

}

});

},

prefill:{
name:data.name,
email:data.email
},

theme:{
color:"#635bff"
}

};

const rzp =
new Razorpay(options);

rzp.open();

}

</script>
"""


@app.route("/pricing")
@login_required
def pricing():

    return render_template_string(
        PRICING_HTML
    )


# ============================================================
# CREATE RAZORPAY ORDER
# ============================================================

@app.route(
    "/create_order/<plan>",
    methods=["POST"]
)
@login_required
def create_order(plan):

    if plan not in PLANS:

        return jsonify({
            "success": False,
            "message": "Invalid plan."
        })


    if not razorpay_client:

        return jsonify({
            "success": False,
            "message":
                "Razorpay is not configured. "
                "Add your Razorpay Test API keys in app.py."
        })


    selected = PLANS[plan]

    amount = (
        selected["price"]
        * 100
    )


    try:

        order = razorpay_client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt":
                "resumeai_"
                + str(session["user_id"])
                + "_"
                + datetime.now().strftime(
                    "%Y%m%d%H%M%S"
                ),
            "notes": {
                "plan": plan,
                "user_id":
                    str(session["user_id"])
            }
        })


        conn = get_db()

        conn.execute("""
            INSERT INTO payments
            (
                user_id,
                plan,
                amount,
                razorpay_order_id,
                status,
                created_at
            )
            VALUES (?,?,?,?,?,?)
        """, (
            session["user_id"],
            plan,
            selected["price"],
            order["id"],
            "created",
            current_time()
        ))

        conn.commit()

        conn.close()


        user = get_current_user()


        return jsonify({
            "success": True,
            "key": RAZORPAY_KEY_ID,
            "amount": amount,
            "order_id": order["id"],
            "description":
                selected["description"],
            "name": user["name"],
            "email": user["email"]
        })


    except Exception as error:

        print(
            "Razorpay order error:",
            error
        )

        return jsonify({
            "success": False,
            "message":
                "Unable to create payment order."
        })


# ============================================================
# PAYMENT SUCCESS
# ============================================================

@app.route(
    "/payment_success",
    methods=["POST"]
)
@login_required
def payment_success():

    data = request.get_json(
        silent=True
    ) or {}


    payment_id = data.get(
        "razorpay_payment_id"
    )

    order_id = data.get(
        "razorpay_order_id"
    )

    signature = data.get(
        "razorpay_signature"
    )


    if not all([
        payment_id,
        order_id,
        signature
    ]):

        return jsonify({
            "success": False,
            "message": "Invalid payment response."
        })


    if not razorpay_client:

        return jsonify({
            "success": False,
            "message": "Razorpay is not configured."
        })


    try:

        # Server-side signature verification
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id":
                order_id,
            "razorpay_payment_id":
                payment_id,
            "razorpay_signature":
                signature
        })


    except Exception as error:

        print(
            "Payment signature error:",
            error
        )

        return jsonify({
            "success": False,
            "message":
                "Payment verification failed."
        })


    conn = get_db()


    payment = conn.execute("""
        SELECT *
        FROM payments
        WHERE razorpay_order_id=?
        AND user_id=?
    """, (
        order_id,
        session["user_id"]
    )).fetchone()


    if not payment:

        conn.close()

        return jsonify({
            "success": False,
            "message":
                "Payment order not found."
        })


    plan = payment["plan"]

    selected = PLANS[plan]


    expiry = (
        datetime.now()
        + timedelta(
            days=selected["days"]
        )
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    conn.execute("""
        UPDATE payments
        SET
            razorpay_payment_id=?,
            status='paid'
        WHERE id=?
    """, (
        payment_id,
        payment["id"]
    ))


    conn.execute("""
        UPDATE users
        SET
            plan=?,
            plan_expiry=?,
            scans_left=?
        WHERE id=?
    """, (
        plan,
        expiry,
        selected["scans"],
        session["user_id"]
    ))


    conn.commit()

    conn.close()


    return jsonify({
        "success": True,
        "message":
            "Payment successful."
    })


# ============================================================
# ADMIN PANEL
# ============================================================

ADMIN_HTML = STYLE + NAV + r"""

<div class="container page">

<div style="
display:flex;
justify-content:space-between;
align-items:center;
margin-bottom:25px">

<div>

<h1 style="font-size:31px">
Admin Control Center
</h1>

<p style="
font-size:12px;
color:#8992a4;
margin-top:5px">
Manage users, scans and payments.
</p>

</div>

<a
href="/dashboard"
class="btn btn-primary">
Scanner
</a>

</div>


<div class="admin-stats">

<div class="card admin-stat">

<div class="admin-label">
USERS
</div>

<div class="admin-number">
{{ total_users }}
</div>

</div>

<div class="card admin-stat">

<div class="admin-label">
SCANS
</div>

<div class="admin-number">
{{ total_scans }}
</div>

</div>

<div class="card admin-stat">

<div class="admin-label">
REVENUE
</div>

<div class="admin-number">
₹{{ revenue }}
</div>

</div>

<div class="card admin-stat">

<div class="admin-label">
AVERAGE ATS
</div>

<div class="admin-number">
{{ average }}%
</div>

</div>

</div>


<div class="card history">

<div class="history-head">

<h3>
Recent Payments
</h3>

<span style="
font-size:10px;
color:#8992a4">
Successful transactions
</span>

</div>


<div class="table-wrap">

<table>

<thead>

<tr>
<th>User</th>
<th>Plan</th>
<th>Amount</th>
<th>Payment ID</th>
<th>Status</th>
<th>Date</th>
</tr>

</thead>

<tbody>

{% for payment in payments %}

<tr>

<td>
{{ payment.user_name }}
<br>
<span style="color:#8992a4">
{{ payment.email }}
</span>
</td>

<td>
{{ payment.plan|upper }}
</td>

<td>
₹{{ payment.amount }}
</td>

<td>
{{ payment.razorpay_payment_id or "Pending" }}
</td>

<td>

<span class="score-pill">
{{ payment.status }}
</span>

</td>

<td>
{{ payment.created_at }}
</td>

</tr>

{% else %}

<tr>
<td colspan="6">
No payments yet.
</td>
</tr>

{% endfor %}

</tbody>

</table>

</div>

</div>


<div class="card history">

<div class="history-head">

<h3>
Users
</h3>

</div>

<div class="table-wrap">

<table>

<thead>

<tr>
<th>ID</th>
<th>Name</th>
<th>Email</th>
<th>Plan</th>
<th>Scans</th>
<th>Joined</th>
</tr>

</thead>

<tbody>

{% for user in users %}

<tr>

<td>
#{{ user.id }}
</td>

<td>
{{ user.name }}
</td>

<td>
{{ user.email }}
</td>

<td>
<span class="score-pill">
{{ user.plan }}
</span>
</td>

<td>
{{ user.scans_left }}
</td>

<td>
{{ user.created_at }}
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>

</div>


<div class="card history">

<div class="history-head">

<h3>
Recent Resume Scans
</h3>

</div>

<div class="table-wrap">

<table>

<thead>

<tr>
<th>User</th>
<th>Resume</th>
<th>Job</th>
<th>ATS</th>
<th>Date</th>
</tr>

</thead>

<tbody>

{% for scan in scans %}

<tr>

<td>
{{ scan.user_name }}
</td>

<td>
{{ scan.resume_name }}
</td>

<td>
{{ scan.job_title or "General" }}
</td>

<td>

<span class="score-pill">
{{ scan.ats_score }}%
</span>

</td>

<td>
{{ scan.scanned_at }}
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>

</div>

</div>
"""


@app.route("/admin")
@admin_required
def admin():

    conn = get_db()


    total_users = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE role='user'
    """).fetchone()[0]


    total_scans = conn.execute("""
        SELECT COUNT(*)
        FROM scans
    """).fetchone()[0]


    revenue = conn.execute("""
        SELECT COALESCE(
            SUM(amount),0
        )
        FROM payments
        WHERE status='paid'
    """).fetchone()[0]


    avg = conn.execute("""
        SELECT AVG(ats_score)
        FROM scans
    """).fetchone()[0]

    average = (
        round(avg)
        if avg
        else 0
    )


    payments = conn.execute("""
        SELECT
            payments.*,
            users.name AS user_name,
            users.email
        FROM payments
        LEFT JOIN users
        ON payments.user_id=users.id
        ORDER BY payments.id DESC
        LIMIT 100
    """).fetchall()


    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY id DESC
        LIMIT 100
    """).fetchall()


    scans = conn.execute("""
        SELECT
            scans.*,
            users.name AS user_name
        FROM scans
        LEFT JOIN users
        ON scans.user_id=users.id
        ORDER BY scans.id DESC
        LIMIT 100
    """).fetchall()


    conn.close()


    return render_template_string(
        ADMIN_HTML,

        total_users=total_users,
        total_scans=total_scans,
        revenue=revenue,
        average=average,
        payments=payments,
        users=users,
        scans=scans
    )


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )

    if session.get("role") == "admin":

        return redirect(
            url_for("admin")
        )

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(error):

    flash(
        "Maximum file size is 10MB.",
        "error"
    )

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    init_db()

    print()
    print("=" * 60)
    print("                 RESUMEAI PRO")
    print("=" * 60)
    print()
    print("Website:")
    print("http://127.0.0.1:5000")
    print()
    print("ADMIN:")
    print("Email    : admin@resumeai.com")
    print("Password : admin123")
    print()
    print("FREE USER:")
    print("3 ATS scans")
    print()
    print("PLANS:")
    print("Pro     : ₹199 / 30 days / 30 scans")
    print("Premium : ₹499 / 30 days / 100 scans")
    print()
    print("Database:")
    print("resumeai_pro.db")
    print("=" * 60)
    print()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )

