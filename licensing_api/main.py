"""FastAPI service for Clerk authentication, trials, Paystack, and admin controls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import Identity, require_admin, require_identity
from .config import settings
from .database import Base, engine, get_db
from .entitlements import as_utc, entitlement_payload, get_or_create_account
from .models import Account, Payment
from .payments import apply_successful_payment, initialize_checkout, valid_webhook_signature

app = FastAPI(title="ManuCodeAI Licensing API", version="1.0.0", docs_url="/api/docs", redoc_url=None)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#090b10"><meta name="description" content="ManuCodeAI is an AI-powered desktop coding assistant that helps you understand, write and improve code from your workspace.">
<title>ManuCodeAI — Your AI coding workspace</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--panel:#11151d;--panel2:#151b25;--line:#252d3a;--text:#f4f6fa;--muted:#a4adbb;--gold:#d4ad68;--gold2:#f0d49d;--blue:#8ba9d8;--max:1160px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(ellipse at 80% 0%,rgba(69,91,133,.18),transparent 38%),var(--bg);color:var(--text);font:15px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}a{color:inherit;text-decoration:none}.container{width:min(var(--max),calc(100% - 48px));margin:auto}.nav{height:76px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.07)}.brand{display:flex;align-items:center;gap:11px;font-weight:750;letter-spacing:-.04em;font-size:20px}.mark{width:34px;height:34px;border:1px solid rgba(212,173,104,.55);border-radius:10px;display:grid;place-items:center;color:var(--gold2);font-weight:800;background:linear-gradient(145deg,#282319,#12151b)}.navlinks{display:flex;align-items:center;gap:28px;color:#bac2cf;font-size:13px}.navlinks a:hover,.footer a:hover{color:var(--gold2)}.nav-cta,.primary{display:inline-flex;align-items:center;justify-content:center;border-radius:9px;background:linear-gradient(135deg,var(--gold2),var(--gold));color:#17130d;font-weight:750;padding:11px 17px;box-shadow:0 8px 30px rgba(212,173,104,.13)}.nav-cta{padding:9px 14px}.hero{padding:94px 0 82px;display:grid;grid-template-columns:1.04fr .96fr;gap:56px;align-items:center}.eyebrow{display:inline-flex;align-items:center;gap:9px;color:var(--gold2);font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.eyebrow:before{content:"";width:22px;height:1px;background:var(--gold)}h1{font-size:clamp(42px,5.4vw,68px);line-height:1.06;letter-spacing:-.065em;margin:20px 0 22px;max-width:650px}h1 em{font-style:normal;color:var(--gold2)}.lead{font-size:17px;line-height:1.8;color:#aeb8c7;max-width:570px}.actions{display:flex;align-items:center;gap:15px;flex-wrap:wrap;margin-top:30px}.secondary{display:inline-flex;align-items:center;justify-content:center;padding:10px 16px;border:1px solid #394252;border-radius:9px;color:#e3e8f0;font-weight:650}.fine{font-size:12px;color:#788394;margin-top:16px}.visual{position:relative;min-height:420px;display:grid;place-items:center}.glow{position:absolute;width:340px;height:340px;border-radius:50%;background:rgba(95,126,180,.16);filter:blur(65px)}.window{position:relative;width:100%;max-width:520px;background:#10151e;border:1px solid #313b4b;border-radius:15px;box-shadow:0 35px 90px rgba(0,0,0,.5);overflow:hidden;transform:perspective(1100px) rotateY(-7deg) rotateX(2deg)}.windowbar{height:42px;border-bottom:1px solid #283140;display:flex;align-items:center;padding:0 15px;gap:7px;color:#8e9aaa;font-size:11px}.dot{width:7px;height:7px;border-radius:50%;background:#536176}.windowtitle{margin-left:8px}.workspace{display:grid;grid-template-columns:116px 1fr;min-height:300px}.sidebar{padding:17px 11px;border-right:1px solid #27303d;color:#8f9aaa;font-size:10px}.sideitem{padding:8px;border-radius:6px;margin-bottom:5px}.sideitem.active{background:#242b36;color:#e5c78e}.editor{padding:19px 18px}.file{font-size:10px;color:#8895a8;margin-bottom:20px}.code{font:11px/2.1 ui-monospace,SFMono-Regular,Consolas,monospace;color:#b9c7d8;white-space:pre}.kw{color:#d8ad75}.fn{color:#9db8e5}.comment{color:#748395}.assistant{margin:18px 0 0 auto;width:82%;padding:12px 14px;border:1px solid #344153;border-radius:9px;background:#171f2b;font-size:10px;color:#cbd4e0}.assistant b{display:block;color:#e8c98f;margin-bottom:5px}.prompt{margin-top:12px;border:1px solid #394557;border-radius:8px;padding:10px;color:#718096;font-size:10px}.section{padding:78px 0;border-top:1px solid rgba(255,255,255,.06)}.section-head{max-width:650px;margin-bottom:34px}.section h2{font-size:clamp(29px,3vw,40px);line-height:1.15;letter-spacing:-.045em;margin:12px 0}.section-head p{color:var(--muted);margin:0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.feature{padding:23px;border:1px solid var(--line);background:linear-gradient(160deg,#141a24,#0f131a);border-radius:13px;min-height:190px}.icon{width:35px;height:35px;border:1px solid #4b4335;border-radius:9px;display:grid;place-items:center;color:var(--gold2);margin-bottom:18px;font-size:15px}.feature h3{margin:0 0 8px;font-size:16px}.feature p{margin:0;color:#9da8b7;font-size:13px;line-height:1.75}.split{display:grid;grid-template-columns:1fr 1fr;gap:60px;align-items:center}.list{list-style:none;padding:0;margin:22px 0}.list li{padding:11px 0;border-bottom:1px solid #222a35;color:#c0c8d4;font-size:14px}.list li:before{content:"✓";color:var(--gold2);margin-right:12px}.price{padding:28px;border:1px solid #4a4438;border-radius:15px;background:linear-gradient(145deg,#191a1c,#11151c);max-width:460px}.price .amount{font-size:42px;font-weight:800;letter-spacing:-.05em;margin:9px 0}.price .amount small{font-size:14px;color:#aab3c0;font-weight:500;letter-spacing:0}.price p{color:#aeb8c7;font-size:13px}.price .trial{display:inline-block;color:var(--gold2);font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.price hr{border:0;border-top:1px solid #303743;margin:20px 0}.price .primary{width:100%;opacity:.7;cursor:not-allowed}.notice{font-size:11px;text-align:center;color:#8792a1;margin-top:11px}.closing{padding:58px 30px;text-align:center;border:1px solid #333b48;border-radius:16px;background:radial-gradient(ellipse at 50% 0%,rgba(212,173,104,.12),transparent 65%),#11151c}.closing h2{margin:0 0 12px}.closing p{color:#aab4c2;margin:0 auto 23px;max-width:580px}.footer{padding:28px 0 38px;color:#8490a0;font-size:12px;display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap}.footerlinks{display:flex;gap:20px}
@media(max-width:850px){.hero{grid-template-columns:1fr;gap:20px;padding:65px 0}.visual{min-height:340px}.window{transform:none}.grid{grid-template-columns:repeat(2,1fr)}.split{gap:30px}}@media(max-width:600px){.container{width:min(var(--max),calc(100% - 32px))}.nav{height:66px}.navlinks>a:not(.nav-cta){display:none}.hero{padding:52px 0 54px}h1{font-size:43px}.lead{font-size:15px}.visual{min-height:280px}.workspace{grid-template-columns:85px 1fr;min-height:270px}.editor{padding:13px 10px}.code{font-size:9px}.assistant{width:95%}.grid,.split{grid-template-columns:1fr}.section{padding:58px 0}.price{max-width:none}.footer{flex-direction:column}}
</style></head><body>
<header class="container nav"><a class="brand" href="/"><span class="mark">M</span>ManuCodeAI</a><nav class="navlinks" aria-label="Main navigation"><a href="#features">Features</a><a href="#workflow">How it works</a><a href="#pricing">Pricing</a><a class="nav-cta" href="#pricing">Get started</a></nav></header>
<main>
<section class="container hero"><div><div class="eyebrow">Your desktop AI coding workspace</div><h1>Build with clarity.<br><em>Code with momentum.</em></h1><p class="lead">ManuCodeAI is an AI-powered desktop coding assistant designed to help you work through code, understand projects and move from an idea to an implementation with less friction.</p><div class="actions"><a class="primary" href="#pricing">Explore ManuCodeAI <span aria-hidden="true">&nbsp;↗</span></a><a class="secondary" href="#features">Discover features</a></div><p class="fine">Built for developers who want an AI assistant close to their workflow.</p></div>
<div class="visual" aria-label="Illustration of a code editor and AI assistant"><div class="glow"></div><div class="window"><div class="windowbar"><i class="dot"></i><i class="dot"></i><i class="dot"></i><span class="windowtitle">ManuCodeAI&nbsp; / &nbsp;workspace</span></div><div class="workspace"><aside class="sidebar"><div class="sideitem active">⌘ &nbsp; Assistant</div><div class="sideitem">▤ &nbsp; Project</div><div class="sideitem">◈ &nbsp; Models</div><div class="sideitem">⚙ &nbsp; Settings</div></aside><div class="editor"><div class="file">src / app.py</div><div class="code"><span class="kw">def</span> <span class="fn">build_something_great</span>():
&nbsp;&nbsp;&nbsp;&nbsp;idea = <span class="kw">"your next project"</span>
&nbsp;&nbsp;&nbsp;&nbsp;<span class="comment"># turn ideas into working code</span>
&nbsp;&nbsp;&nbsp;&nbsp;<span class="kw">return</span> idea</div><div class="assistant"><b>ManuCodeAI Assistant</b>Let's break this task into clear steps, then implement and review the changes.</div><div class="prompt">Ask about your code or describe a change…</div></div></div></div></div></section>
<section class="section" id="features"><div class="container"><div class="section-head"><div class="eyebrow">Made for real development</div><h2>Less friction between thinking and building.</h2><p>A focused desktop experience for the everyday work of software development.</p></div><div class="grid"><article class="feature"><div class="icon">⌘</div><h3>AI coding assistance</h3><p>Work through implementation questions, explore solutions and get help reasoning about code.</p></article><article class="feature"><div class="icon">▤</div><h3>Project-focused workflow</h3><p>Keep your coding work centred around the projects and files you are working on.</p></article><article class="feature"><div class="icon">↗</div><h3>Understand and improve</h3><p>Ask for explanations, investigate unfamiliar code and plan refactoring work.</p></article><article class="feature"><div class="icon">⌁</div><h3>Desktop-first experience</h3><p>Use a dedicated desktop application instead of keeping your development workflow in another browser tab.</p></article><article class="feature"><div class="icon">⚙</div><h3>Model configuration</h3><p>Configure supported AI providers and model access for your development workflow.</p></article><article class="feature"><div class="icon">◷</div><h3>Simple access plan</h3><p>Start with a short trial, then continue with straightforward time-based access.</p></article></div></div></section>
<section class="section" id="workflow"><div class="container split"><div><div class="eyebrow">A practical workflow</div><h2>Bring a coding task. Work through it step by step.</h2><p class="lead" style="font-size:15px">Use ManuCodeAI as a companion while you build: explain what you are trying to do, examine the approach, and iterate on the result.</p><ul class="list"><li>Describe the task or question in plain language.</li><li>Use AI assistance to explore and shape an implementation.</li><li>Review the result and continue refining your project.</li></ul></div><div class="price" id="pricing"><span class="trial">Start with a 15-minute trial</span><div class="amount">KSh 250 <small>/ 14 days</small></div><p>After the trial, purchase 14 days of access at a time. Renew when you need to keep using ManuCodeAI.</p><hr><p>Secure checkout is handled through Paystack. Access is activated after payment verification.</p><a class="secondary" style="width:100%" href="#download">See availability below</a></div></div></section>
<section class="section" id="download"><div class="container"><div class="closing"><div class="eyebrow">ManuCodeAI for desktop</div><h2>Make your next coding session count.</h2><p>Download ManuCodeAI for your operating system. Windows users can check for new versions when the desktop app starts and install updates directly. Linux users can download the latest AppImage below.</p><div class="actions" style="justify-content:center"><a class="primary" id="main-download" href="https://github.com/Manu-del-source/ManuCodeAI/releases/latest/download/ManuCodeAI-Setup.exe">Download ManuCodeAI for Windows</a></div><p class="fine" id="download-help">A simple installer. Download, open the file, and follow the installation prompts.</p><details style="margin-top:14px;color:#aeb8c7;font-size:13px"><summary style="cursor:pointer;color:var(--gold2)">Using another operating system?</summary><div style="display:flex;justify-content:center;gap:18px;flex-wrap:wrap;margin-top:12px"><a href="https://github.com/Manu-del-source/ManuCodeAI/releases/latest/download/ManuCodeAI-Setup.exe" style="text-decoration:underline">Windows installer (.exe)</a><a href="https://github.com/Manu-del-source/ManuCodeAI/releases/latest/download/ManuCodeAI-x86_64.AppImage" style="text-decoration:underline">Linux AppImage</a></div></details></div></div></section>
</main><footer class="container footer"><span>© ManuCodeAI. Built for developers.</span></footer>
<script>
(()=>{const button=document.getElementById('main-download');const help=document.getElementById('download-help');if(!button)return;const platform=(navigator.userAgentData&&navigator.userAgentData.platform)||navigator.platform||navigator.userAgent||'';if(/linux/i.test(platform)){button.href='https://github.com/Manu-del-source/ManuCodeAI/releases/latest/download/ManuCodeAI-x86_64.AppImage';button.textContent='Download ManuCodeAI for Linux';help.textContent='Download the AppImage, make it executable if prompted, then open it.';}else if(/mac/i.test(platform)){button.textContent='Windows version available';button.href='https://github.com/Manu-del-source/ManuCodeAI/releases';help.textContent='A macOS installer is not available yet. Check the releases page for supported downloads.';}})();
</script></body></html>"""


@app.get("/health")
def health() -> dict:
    return {"service": "manucodeai-licensing", "status": "ok"}


@app.get("/api/v1/me")
def get_my_entitlement(identity: Identity = Depends(require_identity), db: Session = Depends(get_db)) -> dict:
    account = get_or_create_account(db, identity.user_id, identity.email)
    return entitlement_payload(account, identity.is_admin)


@app.post("/api/v1/billing/checkout")
def create_checkout(identity: Identity = Depends(require_identity), db: Session = Depends(get_db)) -> dict:
    account = get_or_create_account(db, identity.user_id, identity.email)
    if account.suspended:
        raise HTTPException(status_code=403, detail="This account is suspended.")
    return initialize_checkout(db, account)


@app.get("/api/v1/billing/payments")
def my_payments(identity: Identity = Depends(require_identity), db: Session = Depends(get_db)) -> list[dict]:
    account = get_or_create_account(db, identity.user_id, identity.email)
    rows = db.scalars(
        select(Payment).where(Payment.account_id == account.id)
        .order_by(Payment.created_at.desc()).limit(50)
    )
    return [{
        "reference": row.reference, "amount_kes": row.amount / 100,
        "currency": row.currency, "status": row.status,
        "created_at": row.created_at.isoformat(),
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
    } for row in rows]


@app.get("/payment/complete", response_class=HTMLResponse)
def payment_complete() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>ManuCodeAI payment</title><style>
    body{margin:0;background:#0d0f12;color:#eceef2;font:16px Segoe UI,sans-serif;display:grid;
    min-height:100vh;place-items:center}.card{max-width:520px;margin:24px;padding:32px;background:#15181d;
    border:1px solid #30343c;border-radius:14px}h1{color:#e1bf7c}p{line-height:1.6;color:#b7bdc7}
    </style></head><body><main class="card"><h1>Payment submitted</h1>
    <p>Paystack has returned you to ManuCodeAI. Your payment is being verified securely.
    Return to the desktop application and select <strong>Refresh access</strong>.
    Access is activated only after the server confirms the transaction.</p></main></body></html>"""


@app.post("/api/v1/paystack/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    raw_body = await request.body()
    if not valid_webhook_signature(raw_body, x_paystack_signature):
        raise HTTPException(status_code=401, detail="Invalid Paystack webhook signature.")
    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from error
    if event.get("event") == "charge.success":
        reference = (event.get("data") or {}).get("reference")
        if not reference:
            raise HTTPException(status_code=400, detail="Webhook is missing transaction reference.")
        apply_successful_payment(db, reference)
    return {"received": True}


@app.get("/api/v1/admin/overview")
def admin_overview(_: Identity = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    total = db.scalar(select(func.count(Account.id))) or 0
    paying = db.scalar(select(func.count(Account.id)).where(Account.paid_until > now)) or 0
    trials = db.scalar(select(func.count(Account.id)).where(
        Account.trial_ends_at > now, Account.paid_until.is_(None)
    )) or 0
    paid_count = db.scalar(select(func.count(Payment.id)).where(Payment.status == "success")) or 0
    revenue = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == "success"
    )) or 0
    return {
        "total_users": total, "paying_users": paying, "trial_users": trials,
        "successful_payments": paid_count, "revenue_kes": revenue / 100,
    }


@app.get("/api/v1/admin/config")
def admin_config(_: Identity = Depends(require_admin)) -> dict:
    key = settings.paystack_secret_key
    mode = "test" if key.startswith("sk_test_") else "live" if key.startswith("sk_live_") else "unknown"
    return {
        "provider": "Paystack",
        "configured": bool(key),
        "mode": mode if key else "not_configured",
        "callback_configured": bool(settings.paystack_callback_url),
        "webhook_path": "/api/v1/paystack/webhook",
        "trial_minutes": settings.trial_minutes,
        "subscription_amount_kes": settings.subscription_amount_subunits / 100,
        "subscription_days": settings.subscription_days,
        "currency": settings.currency,
    }


@app.get("/api/v1/admin/users")
def admin_users(
    _: Identity = Depends(require_admin), db: Session = Depends(get_db), limit: int = 100,
) -> list[dict]:
    rows = db.scalars(select(Account).order_by(Account.created_at.desc()).limit(min(max(limit, 1), 500)))
    now = datetime.now(timezone.utc)
    return [{
        "user_id": account.clerk_user_id,
        "email": account.email,
        "created_at": as_utc(account.created_at).isoformat(),
        "trial_ends_at": as_utc(account.trial_ends_at).isoformat(),
        "paid_until": as_utc(account.paid_until).isoformat() if account.paid_until else None,
        "suspended": account.suspended,
        "status": (
            "suspended" if account.suspended else
            "subscribed" if account.paid_until and as_utc(account.paid_until) > now else
            "trial" if as_utc(account.trial_ends_at) > now else "expired"
        ),
    } for account in rows]


@app.get("/api/v1/admin/payments")
def admin_payments(
    _: Identity = Depends(require_admin), db: Session = Depends(get_db), limit: int = 200,
) -> list[dict]:
    rows = db.scalars(
        select(Payment).order_by(Payment.created_at.desc()).limit(min(max(limit, 1), 500))
    )
    return [{
        "reference": payment.reference,
        "email": payment.account.email,
        "amount_kes": payment.amount / 100,
        "currency": payment.currency,
        "status": payment.status,
        "created_at": as_utc(payment.created_at).isoformat(),
        "paid_at": as_utc(payment.paid_at).isoformat() if payment.paid_at else None,
    } for payment in rows]


class UserAccessUpdate(BaseModel):
    suspended: bool | None = None


@app.patch("/api/v1/admin/users/{clerk_user_id}")
def update_user_access(
    clerk_user_id: str,
    payload: UserAccessUpdate,
    _: Identity = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    account = db.scalar(select(Account).where(Account.clerk_user_id == clerk_user_id))
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload.suspended is not None:
        account.suspended = payload.suspended
    db.commit()
    return {"user_id": account.clerk_user_id, "suspended": account.suspended}


class GrantAccess(BaseModel):
    days: int = Field(ge=1, le=3650)


@app.post("/api/v1/admin/users/{clerk_user_id}/grant")
def grant_user_access(
    clerk_user_id: str,
    payload: GrantAccess,
    _: Identity = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    account = db.scalar(select(Account).where(Account.clerk_user_id == clerk_user_id))
    if not account:
        raise HTTPException(status_code=404, detail="User not found.")
    now = datetime.now(timezone.utc)
    current_expiry = as_utc(account.paid_until) if account.paid_until else None
    base = current_expiry if current_expiry and current_expiry > now else now
    account.paid_until = base + timedelta(days=payload.days)
    account.suspended = False
    db.commit()
    return {"user_id": account.clerk_user_id, "paid_until": as_utc(account.paid_until).isoformat()}


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> str:
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ManuCodeAI Admin</title><style>
:root{color-scheme:dark;font-family:Inter,Segoe UI,sans-serif;background:#0d0f12;color:#eceef2}
body{margin:0;padding:32px}.wrap{max-width:1240px;margin:auto}.muted{color:#9ba2ad}h1{font-size:30px}
.card{background:#15181d;border:1px solid #2b3038;border-radius:12px;padding:20px;margin:16px 0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.stat strong{display:block;font-size:27px;color:#e1bf7c;margin-top:8px}
button{background:#c49a52;color:#111;border:0;padding:9px 12px;border-radius:7px;font-weight:700;cursor:pointer}
button.secondary{background:#343a43;color:#eee}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:12px;border-bottom:1px solid #30343c;font-size:13px}
#dashboard{display:none}.scroll{overflow:auto}h2{font-size:18px}
</style></head><body><main class="wrap"><h1>ManuCodeAI <span style="color:#c49a52">Admin</span></h1>
<p class="muted">User access, trials, subscriptions and payment overview.</p>
<section class="card"><div id="signin"></div><button id="load" style="display:none">Refresh dashboard</button><p id="message" class="muted"></p></section>
<section id="dashboard"><div class="stats" id="stats"></div>
<section class="card"><h2>Payment configuration</h2><div id="payment-config" class="muted">Loading configuration…</div></section>\n<section class="card"><h2>Users</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Status</th><th>Trial ends</th><th>Paid until</th><th>Actions</th></tr></thead><tbody id="users"></tbody></table></div></section>
<section class="card"><h2>Payments</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Reference</th><th>Amount</th><th>Status</th><th>Created</th><th>Paid</th></tr></thead><tbody id="payments"></tbody></table></div></section>
</section></main>
<script>
const publishableKey=__CLERK_PUBLISHABLE_KEY__;const message=document.getElementById('message');let clerkToken=null;
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','Authorization':'Bearer '+clerkToken,...(options.headers||{})}});const b=await r.json();if(!r.ok)throw new Error(b.detail||'Request failed');return b;}
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function loadDashboard(){try{const [o,u,p,cfg]=await Promise.all([api('/api/v1/admin/overview'),api('/api/v1/admin/users'),api('/api/v1/admin/payments'),api('/api/v1/admin/config')]);
document.getElementById('stats').innerHTML=[['Users',o.total_users],['Active trials',o.trial_users],['Paying users',o.paying_users],['Successful payments',o.successful_payments],['Revenue','KSh '+o.revenue_kes.toLocaleString()]].map(([k,v])=>'<div class="card stat"><span class="muted">'+k+'</span><strong>'+v+'</strong></div>').join('');
document.getElementById('users').innerHTML=u.map(x=>'<tr><td>'+esc(x.email)+'</td><td>'+esc(x.status)+'</td><td>'+new Date(x.trial_ends_at).toLocaleString()+'</td><td>'+(x.paid_until?new Date(x.paid_until).toLocaleString():'—')+'</td><td><button data-action="grant" data-user-id="'+esc(x.user_id)+'">Grant 14 days</button> <button class="secondary" data-action="suspend" data-user-id="'+esc(x.user_id)+'" data-suspended="'+(!x.suspended)+'">'+(x.suspended?'Restore':'Suspend')+'</button></td></tr>').join('');
document.getElementById('payment-config').innerHTML='<p>Paystack secret: <strong>'+esc(cfg.configured?'Configured':'Missing')+'</strong> ('+esc(cfg.mode)+')</p><p>Checkout callback: <strong>'+esc(cfg.callback_configured?'Configured':'Missing')+'</strong></p><p>Webhook URL: <code>'+esc(location.origin+cfg.webhook_path)+'</code></p><p>Plan: KSh '+cfg.subscription_amount_kes+' for '+cfg.subscription_days+' days · trial '+cfg.trial_minutes+' minutes.</p><p class=\"muted\">Set or rotate secret keys in Render environment variables. The dashboard never displays or stores secret keys.</p>';
document.getElementById('payments').innerHTML=p.map(x=>'<tr><td>'+esc(x.email)+'</td><td>'+esc(x.reference)+'</td><td>KSh '+x.amount_kes.toLocaleString()+'</td><td>'+esc(x.status)+'</td><td>'+new Date(x.created_at).toLocaleString()+'</td><td>'+(x.paid_at?new Date(x.paid_at).toLocaleString():'—')+'</td></tr>').join('');
document.getElementById('dashboard').style.display='block';message.textContent='Dashboard refreshed.';}catch(e){message.textContent=e.message;}}
async function grant(id){try{await api('/api/v1/admin/users/'+decodeURIComponent(id)+'/grant',{method:'POST',body:JSON.stringify({days:14})});await loadDashboard();}catch(e){message.textContent=e.message;}}
async function suspend(id,v){try{await api('/api/v1/admin/users/'+decodeURIComponent(id),{method:'PATCH',body:JSON.stringify({suspended:v})});await loadDashboard();}catch(e){message.textContent=e.message;}}
document.getElementById('users').addEventListener('click',e=>{const b=e.target.closest('button[data-action]');if(!b)return;const id=encodeURIComponent(b.dataset.userId);if(b.dataset.action==='grant')grant(id);else if(b.dataset.action==='suspend')suspend(id,b.dataset.suspended==='true');});
document.getElementById('load').onclick=loadDashboard;
(async()=>{if(!publishableKey){message.textContent='Set CLERK_PUBLISHABLE_KEY to enable administrator sign-in.';return;}
const s=document.createElement('script');s.async=true;s.src='https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js';s.setAttribute('data-clerk-publishable-key',publishableKey);
s.onload=async()=>{await Clerk.load();if(Clerk.user){clerkToken=await Clerk.session.getToken();document.getElementById('load').style.display='inline-block';await loadDashboard();}else{Clerk.mountSignIn(document.getElementById('signin'),{forceRedirectUrl:location.href});}};
s.onerror=()=>message.textContent='Could not load Clerk authentication.';document.head.appendChild(s);})();
</script></body></html>"""
    return html.replace("__CLERK_PUBLISHABLE_KEY__", json.dumps(settings.clerk_publishable_key))
