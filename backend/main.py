import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import engine, get_db
from models import Base, User, Post
from schemas import UserCreate, User as UserSchema, PostCreate, Post as PostSchema


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FastAPI Backend", version="1.0.0", lifespan=lifespan)

cors_origins_env = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001",
)
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "Welcome to FastAPI Backend"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


# ============= USER ENDPOINTS =============

@app.get("/api/users", response_model=list[UserSchema])
async def list_users(skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Get all users with pagination."""
    query = select(User).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/api/users/count")
async def count_users(db: AsyncSession = Depends(get_db)):
    """Get total user count."""
    query = select(func.count(User.id))
    result = await db.execute(query)
    count = result.scalar()
    return {"count": count}


@app.get("/api/users/{user_id}", response_model=UserSchema)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific user by ID."""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.post("/api/users", response_model=UserSchema)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create a new user."""
    # Check if user already exists
    existing = await db.execute(
        select(User).where((User.username == user.username) | (User.email == user.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already exists")

    db_user = User(**user.model_dump())
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@app.put("/api/users/{user_id}", response_model=UserSchema)
async def update_user(user_id: int, user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Update a user."""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    for key, value in user.model_dump().items():
        setattr(db_user, key, value)

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a user."""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(db_user)
    await db.commit()
    return {"message": "User deleted"}


# ============= POST ENDPOINTS =============

@app.get("/api/posts", response_model=list[PostSchema])
async def list_posts(skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Get all posts with pagination."""
    query = select(Post).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/api/posts/count")
async def count_posts(db: AsyncSession = Depends(get_db)):
    """Get total post count."""
    query = select(func.count(Post.id))
    result = await db.execute(query)
    count = result.scalar()
    return {"count": count}


@app.get("/api/posts/{post_id}", response_model=PostSchema)
async def get_post(post_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific post by ID."""
    query = select(Post).where(Post.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@app.post("/api/posts", response_model=PostSchema)
async def create_post(post: PostCreate, db: AsyncSession = Depends(get_db)):
    """Create a new post."""
    # Verify user exists
    user_query = select(User).where(User.id == post.user_id)
    user_result = await db.execute(user_query)
    if not user_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User not found")

    db_post = Post(**post.model_dump())
    db.add(db_post)
    await db.commit()
    await db.refresh(db_post)
    return db_post


@app.put("/api/posts/{post_id}", response_model=PostSchema)
async def update_post(post_id: int, post: PostCreate, db: AsyncSession = Depends(get_db)):
    """Update a post."""
    query = select(Post).where(Post.id == post_id)
    result = await db.execute(query)
    db_post = result.scalar_one_or_none()
    if not db_post:
        raise HTTPException(status_code=404, detail="Post not found")

    for key, value in post.model_dump().items():
        setattr(db_post, key, value)

    db.add(db_post)
    await db.commit()
    await db.refresh(db_post)
    return db_post


@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a post."""
    query = select(Post).where(Post.id == post_id)
    result = await db.execute(query)
    db_post = result.scalar_one_or_none()
    if not db_post:
        raise HTTPException(status_code=404, detail="Post not found")

    await db.delete(db_post)
    await db.commit()
    return {"message": "Post deleted"}
