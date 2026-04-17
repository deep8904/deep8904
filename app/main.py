from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
import secrets

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer
from passlib.context import CryptContext
from sqlmodel import Field, Relationship, SQLModel, Session, create_engine, select

DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

SECRET_KEY = "change-me-in-production"
serializer = URLSafeSerializer(SECRET_KEY, salt="session")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class Follow(SQLModel, table=True):
    follower_id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)
    followed_id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    display_name: str
    bio: str = ""
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    photos: list["Photo"] = Relationship(back_populates="owner")


class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    caption: str = ""
    image_path: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)

    owner: User = Relationship(back_populates="photos")


app = FastAPI(title="Fedishare")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_current_user(request: Request) -> Optional[User]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        payload = serializer.loads(token)
        user_id = payload.get("user_id")
    except Exception:
        return None

    with Session(engine) as session:
        return session.get(User, user_id)


def save_upload(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Unsupported image format")

    filename = f"{secrets.token_hex(12)}{suffix}"
    out_path = UPLOAD_DIR / filename
    with out_path.open("wb") as out:
        out.write(file.file.read())
    return filename


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = get_current_user(request)
    photos = []

    with Session(engine) as session:
        if user:
            followed = session.exec(
                select(Follow.followed_id).where(Follow.follower_id == user.id)
            ).all()
            allowed = set(followed + [user.id])
            photos = session.exec(
                select(Photo, User)
                .join(User, Photo.owner_id == User.id)
                .where(Photo.owner_id.in_(allowed))
                .order_by(Photo.created_at.desc())
                .limit(100)
            ).all()
        else:
            photos = session.exec(
                select(Photo, User)
                .join(User, Photo.owner_id == User.id)
                .order_by(Photo.created_at.desc())
                .limit(100)
            ).all()

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "current_user": user, "photos": photos},
    )


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    user = get_current_user(request)
    with Session(engine) as session:
        photos = session.exec(
            select(Photo, User)
            .join(User, Photo.owner_id == User.id)
            .order_by(Photo.created_at.desc())
            .limit(120)
        ).all()
    return templates.TemplateResponse(
        "explore.html", {"request": request, "current_user": user, "photos": photos}
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "current_user": get_current_user(request)})


@app.post("/register")
def register(
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
):
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")

        user = User(
            username=username.strip().lower(),
            display_name=display_name.strip(),
            password_hash=pwd_context.hash(password),
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("session", serializer.dumps({"user_id": user.id}), httponly=True, samesite="lax")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "current_user": get_current_user(request)})


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username.strip().lower())).first()
        if not user or not pwd_context.verify(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("session", serializer.dumps({"user_id": user.id}), httponly=True, samesite="lax")
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session")
    return response


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("upload.html", {"request": request, "current_user": user})


@app.post("/upload")
def upload(request: Request, caption: str = Form(""), photo: UploadFile | None = None):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not photo:
        raise HTTPException(status_code=400, detail="Missing photo")

    filename = save_upload(photo)
    with Session(engine) as session:
        session.add(Photo(owner_id=user.id, caption=caption.strip(), image_path=filename))
        session.commit()

    return RedirectResponse(url=f"/u/{user.username}", status_code=303)


@app.get("/u/{username}", response_class=HTMLResponse)
def profile(request: Request, username: str):
    current_user = get_current_user(request)
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username.lower())).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        photos = session.exec(
            select(Photo).where(Photo.owner_id == user.id).order_by(Photo.created_at.desc())
        ).all()
        followers_count = len(session.exec(select(Follow).where(Follow.followed_id == user.id)).all())
        following_count = len(session.exec(select(Follow).where(Follow.follower_id == user.id)).all())

        is_following = False
        if current_user and current_user.id != user.id:
            is_following = session.get(Follow, (current_user.id, user.id)) is not None

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "current_user": current_user,
            "profile_user": user,
            "photos": photos,
            "followers_count": followers_count,
            "following_count": following_count,
            "is_following": is_following,
        },
    )


@app.post("/u/{username}/follow")
def follow_user(request: Request, username: str):
    current_user = get_current_user(request)
    if not current_user:
        return RedirectResponse("/login", status_code=303)

    with Session(engine) as session:
        target = session.exec(select(User).where(User.username == username.lower())).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target.id == current_user.id:
            return RedirectResponse(f"/u/{username}", status_code=303)

        existing = session.get(Follow, (current_user.id, target.id))
        if existing:
            session.delete(existing)
        else:
            session.add(Follow(follower_id=current_user.id, followed_id=target.id))
        session.commit()

    return RedirectResponse(f"/u/{username}", status_code=303)


@app.get("/media/{filename}")
def media(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


# --- Minimal federation support (ActivityPub + WebFinger) ---

def actor_url(request: Request, username: str) -> str:
    return str(request.url_for("activitypub_actor", username=username))


@app.get("/.well-known/webfinger")
def webfinger(request: Request, resource: str):
    if not resource.startswith("acct:"):
        raise HTTPException(status_code=400, detail="Unsupported resource")

    acct = resource.replace("acct:", "", 1)
    username, _, host = acct.partition("@")
    if not username or not host:
        raise HTTPException(status_code=400, detail="Invalid acct")

    if host != request.url.hostname:
        raise HTTPException(status_code=404, detail="Not found on this server")

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username.lower())).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

    return JSONResponse(
        {
            "subject": resource,
            "links": [
                {
                    "rel": "self",
                    "type": "application/activity+json",
                    "href": actor_url(request, username.lower()),
                }
            ],
        }
    )


@app.get("/u/{username}/actor", name="activitypub_actor")
def activitypub_actor(request: Request, username: str):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username.lower())).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

    base = str(request.base_url).rstrip("/")
    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": actor_url(request, user.username),
        "type": "Person",
        "preferredUsername": user.username,
        "name": user.display_name,
        "summary": user.bio,
        "inbox": f"{base}/u/{user.username}/inbox",
        "outbox": f"{base}/u/{user.username}/outbox",
    }


@app.get("/u/{username}/outbox")
def activitypub_outbox(request: Request, username: str):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username.lower())).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        photos = session.exec(
            select(Photo).where(Photo.owner_id == user.id).order_by(Photo.created_at.desc()).limit(20)
        ).all()

    base = str(request.base_url).rstrip("/")
    items = []
    for photo in photos:
        note_id = f"{base}/photos/{photo.id}"
        items.append(
            {
                "id": f"{note_id}#create",
                "type": "Create",
                "actor": actor_url(request, user.username),
                "published": photo.created_at.isoformat(),
                "object": {
                    "id": note_id,
                    "type": "Note",
                    "content": photo.caption,
                    "attachment": [
                        {
                            "type": "Image",
                            "mediaType": "image/jpeg",
                            "url": f"{base}/media/{photo.image_path}",
                        }
                    ],
                },
            }
        )

    return {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{base}/u/{user.username}/outbox",
        "type": "OrderedCollection",
        "totalItems": len(items),
        "orderedItems": items,
    }


@app.post("/u/{username}/inbox")
async def activitypub_inbox(username: str, request: Request):
    # MVP: accept and log inbound activities for future processing.
    payload = await request.body()
    log_path = DATA_DIR / "inbox.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {username}: {payload.decode('utf-8', errors='ignore')}\n")

    return {"status": "accepted"}


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "current_user": get_current_user(request)})
