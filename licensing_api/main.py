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
<section class="card"><h2>Users</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Status</th><th>Trial ends</th><th>Paid until</th><th>Actions</th></tr></thead><tbody id="users"></tbody></table></div></section>
<section class="card"><h2>Payments</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Reference</th><th>Amount</th><th>Status</th><th>Created</th><th>Paid</th></tr></thead><tbody id="payments"></tbody></table></div></section>
</section></main>
<script>
const publishableKey=__CLERK_PUBLISHABLE_KEY__;const message=document.getElementById('message');let clerkToken=null;
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','Authorization':'Bearer '+clerkToken,...(options.headers||{})}});const b=await r.json();if(!r.ok)throw new Error(b.detail||'Request failed');return b;}
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function loadDashboard(){try{const [o,u,p]=await Promise.all([api('/api/v1/admin/overview'),api('/api/v1/admin/users'),api('/api/v1/admin/payments')]);
document.getElementById('stats').innerHTML=[['Users',o.total_users],['Active trials',o.trial_users],['Paying users',o.paying_users],['Successful payments',o.successful_payments],['Revenue','KSh '+o.revenue_kes.toLocaleString()]].map(([k,v])=>'<div class="card stat"><span class="muted">'+k+'</span><strong>'+v+'</strong></div>').join('');
document.getElementById('users').innerHTML=u.map(x=>'<tr><td>'+esc(x.email)+'</td><td>'+esc(x.status)+'</td><td>'+new Date(x.trial_ends_at).toLocaleString()+'</td><td>'+(x.paid_until?new Date(x.paid_until).toLocaleString():'—')+'</td><td><button onclick="grant(\''+encodeURIComponent(x.user_id)+'\')">Grant 14 days</button> <button class="secondary" onclick="suspend(\''+encodeURIComponent(x.user_id)+'\','+(!x.suspended)+')">'+(x.suspended?'Restore':'Suspend')+'</button></td></tr>').join('');
document.getElementById('payments').innerHTML=p.map(x=>'<tr><td>'+esc(x.email)+'</td><td>'+esc(x.reference)+'</td><td>KSh '+x.amount_kes.toLocaleString()+'</td><td>'+esc(x.status)+'</td><td>'+new Date(x.created_at).toLocaleString()+'</td><td>'+(x.paid_at?new Date(x.paid_at).toLocaleString():'—')+'</td></tr>').join('');
document.getElementById('dashboard').style.display='block';message.textContent='Dashboard refreshed.';}catch(e){message.textContent=e.message;}}
async function grant(id){try{await api('/api/v1/admin/users/'+decodeURIComponent(id)+'/grant',{method:'POST',body:JSON.stringify({days:14})});await loadDashboard();}catch(e){message.textContent=e.message;}}
async function suspend(id,v){try{await api('/api/v1/admin/users/'+decodeURIComponent(id),{method:'PATCH',body:JSON.stringify({suspended:v})});await loadDashboard();}catch(e){message.textContent=e.message;}}
document.getElementById('load').onclick=loadDashboard;
(async()=>{if(!publishableKey){message.textContent='Set CLERK_PUBLISHABLE_KEY to enable administrator sign-in.';return;}
const s=document.createElement('script');s.async=true;s.src='https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js';s.setAttribute('data-clerk-publishable-key',publishableKey);
s.onload=async()=>{await Clerk.load();if(Clerk.user){clerkToken=await Clerk.session.getToken();document.getElementById('load').style.display='inline-block';await loadDashboard();}else{Clerk.mountSignIn(document.getElementById('signin'),{forceRedirectUrl:location.href});}};
s.onerror=()=>message.textContent='Could not load Clerk authentication.';document.head.appendChild(s);})();
</script></body></html>"""
    return html.replace("__CLERK_PUBLISHABLE_KEY__", json.dumps(settings.clerk_publishable_key))
