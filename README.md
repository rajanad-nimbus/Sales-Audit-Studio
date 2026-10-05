# Full Stack Application: FastAPI + PostgreSQL + Next.js

A modern full-stack web application with a FastAPI backend, PostgreSQL database, and Next.js frontend.

## Tech Stack

- **Backend**: FastAPI 0.142
- **Database**: PostgreSQL 18
- **Frontend**: Next.js 16 with React 19
- **ORM**: SQLAlchemy with async support
- **API Client**: Axios

## Project Structure

```
.
├── backend/                 # FastAPI backend application
│   ├── main.py            # Main FastAPI application
│   ├── models.py          # SQLAlchemy ORM models
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── database.py        # Database connection and session management
│   └── requirements.txt    # Python dependencies
├── frontend/              # Next.js frontend application
│   ├── app/              # Next.js app directory
│   │   ├── page.tsx      # Home page
│   │   ├── layout.tsx    # Root layout
│   │   └── globals.css   # Global styles
│   ├── package.json      # Node.js dependencies
│   ├── tsconfig.json     # TypeScript configuration
│   └── next.config.ts    # Next.js configuration
├── docker-compose.yml    # Docker services (PostgreSQL, pgAdmin)
├── .env.example         # Environment variables template
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Prerequisites

- Python 3.10+ (3.12+ recommended)
- Node.js 20+ and npm/yarn
- Docker and Docker Compose (for PostgreSQL)

## Setup Instructions

### 1. Clone or Initialize the Project

```bash
cd /path/to/project
```

### 2. Set Up Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your configuration:
```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname
SQL_ECHO=False
ENVIRONMENT=development
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 3. Start PostgreSQL with Docker

```bash
docker-compose up -d
```

This starts:
- PostgreSQL on `localhost:5432`
- 

Verify PostgreSQL is running:
```bash
docker-compose ps
```

### 4. Set Up Backend

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 5. Set Up Frontend

```bash
cd frontend

# Install dependencies
npm install

# Or with yarn
yarn install
```

## Running the Application

### Start Backend

```bash
cd backend

# With virtual environment activated
python -m uvicorn main:app --reload
```

Backend API: `http://localhost:8000`
API Docs: `http://localhost:8000/docs`

### Start Frontend

In a new terminal:

```bash
cd frontend

npm run dev
# Or with yarn
yarn dev
```

Frontend: `http://localhost:3000`

## API Endpoints

- `GET /` - Welcome message
- `GET /api/health` - Health check endpoint
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - ReDoc documentation

## Database Management

### Access pgAdmin

1. Open `http://localhost:5050`
2. Login with:
   - Email: `admin@example.com`
   - Password: `admin`
3. Add a new server:
   - Hostname: `postgres`
   - Username: `user`
   - Password: `password`
   - Database: `dbname`

### Alembic Migrations (Optional Setup)

To set up database migrations:

```bash
cd backend

# Install alembic
pip install alembic

# Initialize migrations
alembic init migrations

# Create first migration
alembic revision --autogenerate -m "Initial migration"

# Apply migrations
alembic upgrade head
```

## Development Workflow

### Adding Backend Routes

Edit `backend/main.py` to add new routes:

```python
@app.get("/api/items")
async def get_items():
    return {"items": []}
```

### Adding Database Models

Edit `backend/models.py` to define new tables, then the database will auto-create them on startup.

### Adding Frontend Pages

Create new files in `frontend/app/`:

```bash
# Create a new route
touch frontend/app/about/page.tsx
```

## Troubleshooting

### PostgreSQL Connection Error

Check if PostgreSQL is running:
```bash
docker-compose ps
docker-compose logs postgres
```

Restart PostgreSQL:
```bash
docker-compose down
docker-compose up -d
```

### Backend Port Already in Use

Kill the process or use a different port:
```bash
python -m uvicorn main:app --reload --port 8001
```

### Frontend Build Errors

Clear Next.js cache:
```bash
cd frontend
rm -rf .next
npm run build
```

### Module Not Found Errors

Reinstall dependencies:

**Backend:**
```bash
cd backend
source venv/bin/activate
pip install --force-reinstall -r requirements.txt
```

**Frontend:**
```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
```

## Production Deployment

### Backend

Use Gunicorn with Uvicorn workers:

```bash
pip install gunicorn

gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000
```

### Frontend

Build the production version:

```bash
cd frontend
npm run build
npm start
```

Or deploy to Vercel:
```bash
npm i -g vercel
vercel
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://user:password@localhost:5432/dbname` |
| `SQL_ECHO` | Log all SQL statements | `False` |
| `ENVIRONMENT` | Application environment | `development` |
| `NEXT_PUBLIC_API_URL` | Backend API URL (exposed to client) | `http://localhost:8000` |

## Next Steps

1. Implement API endpoints for your use cases
2. Add more database models as needed
3. Create frontend pages and components
4. Set up authentication (JWT, OAuth, etc.)
5. Add testing with pytest (backend) and Jest (frontend)
6. Configure CI/CD pipeline

## License

MIT

## Support

For issues or questions, check the official documentation:
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy Docs](https://docs.sqlalchemy.org/)
- [Next.js Docs](https://nextjs.org/docs)
- [PostgreSQL Docs](https://www.postgresql.org/docs/)
