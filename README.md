# TaskFlow — Ứng dụng Quản lý Công việc Cá nhân

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-red.svg)](https://www.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://www.docker.com/)

---

## Mục lục

- [Tổng quan dự án](#tổng-quan-dự-án)
- [Tính năng chính](#tính-năng-chính)
- [Kiến trúc hệ thống](#kiến-trúc-hệ-thống)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt và chạy local](#cài-đặt-và-chạy-local)
- [Biến môi trường](#biến-môi-trường)
- [API Documentation](#api-documentation)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Database Migrations](#database-migrations)
- [Testing](#testing)
- [Deployment](#deployment)

---

## Tổng quan dự án

**TaskFlow** là ứng dụng quản lý công việc cá nhân (Personal Task Manager) được xây dựng với FastAPI và PostgreSQL. Hệ thống hỗ trợ xác thực qua AWS Cognito, lưu trữ avatar trên S3, gửi thông báo qua SNS/FCM/APNs, và lên lịch nhắc nhở với EventBridge Scheduler.

**Phiên bản:** 1.1  
**Ngày cập nhật:** 24-01-2025

---

## Tính năng chính

| Nhóm tính năng | Mô tả |
|----------------|-------|
| **Xác thực** | Đăng ký bằng email, xác minh email, đăng nhập, reset mật khẩu qua AWS Cognito |
| **Quản lý Task** | Tạo, xem, chỉnh sửa, xóa task với soft-delete và undo trong 5 giây; hỗ trợ filter, sort, full-text search |
| **Trạng thái Task** | PENDING → OVERDUE (tự động mỗi 15 phút) → COMPLETED; undo hoàn thành trong 24 giờ |
| **Danh mục** | Tạo tối đa 50 danh mục; xóa tự động chuyển task sang Uncategorized |
| **Nhắc nhở** | Tối đa 3 nhắc nhở/task; hỗ trợ one-time và recurring (hàng ngày/tuần/tháng); lên lịch qua EventBridge |
| **Thông báo** | Push notification qua FCM/APNs; fallback email qua SES; ghi log delivery status |
| **Dashboard** | Tổng quan task hôm nay, sắp tới 7 ngày, quá hạn; tỷ lệ hoàn thành; streak liên tiếp |
| **Profile** | Cập nhật tên, avatar; upload ảnh trực tiếp lên S3 qua pre-signed URL |

---

## Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────────────────┐
│                          Client (Mobile/Web)                    │
└────────────────────────────────┬────────────────────────────────┘
                                 │ HTTPS
┌────────────────────────────────▼────────────────────────────────┐
│                     AWS API Gateway / FastAPI                   │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
│  │  Auth Router│  │  Task Router│  │  Dashboard Router    │   │
│  │  /auth/*    │  │  /tasks/*   │  │  /dashboard, /stats  │   │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘   │
│         │                │                     │               │
│  ┌──────▼──────────────────────────────────────▼───────────┐   │
│  │              FastAPI Application Layer                   │   │
│  │  Services: task, category, reminder, notification,      │   │
│  │           dashboard, user                               │   │
│  └──────────────────────────────┬───────────────────────────┘  │
└─────────────────────────────────┼───────────────────────────────┘
                                  │
          ┌───────────────────────┼────────────────────────┐
          │                       │                        │
┌─────────▼──────┐   ┌────────────▼─────────┐  ┌─────────▼──────┐
│   PostgreSQL   │   │   AWS Services       │  │  EventBridge   │
│   (RDS)        │   │                      │  │  Scheduler     │
│                │   │  - Cognito (Auth)    │  │                │
│  - users       │   │  - S3 (Avatar)       │  │  - Reminder    │
│  - tasks       │   │  - SES (Email)       │  │    schedules   │
│  - categories  │   │  - SNS (Push)        │  │  - Overdue     │
│  - reminders   │   │  - SQS (Buffer)      │  │    batch job   │
└────────────────┘   └──────────────────────┘  └────────────────┘
```

### Các thành phần chính

| Thành phần | Công nghệ | Mục đích |
|------------|-----------|----------|
| **Backend API** | FastAPI 0.110+, Python 3.11+ | Xử lý request/response, business logic |
| **ORM** | SQLAlchemy 2.x (async) | Tương tác database với type-safe models |
| **Database** | PostgreSQL 15+ | Lưu trữ chính cho task, category, reminder |
| **Authentication** | AWS Cognito User Pools | Đăng ký, đăng nhập, quản lý session |
| **Email** | AWS SES | Gửi verification email, reset password, reminder |
| **File Storage** | AWS S3 | Lưu trữ avatar người dùng |
| **Push Notification** | AWS SNS + FCM/APNs | Gửi nhắc nhở đến mobile devices |
| **Task Queue** | AWS SQS | Buffer cho burst notifications |
| **Scheduling** | AWS EventBridge Scheduler | Lên lịch nhắc nhở và batch overdue update |
| **Migration** | Alembic | Quản lý schema database |

---

## Yêu cầu hệ thống

### Môi trường development local

- **Python** 3.11 trở lên
- **Docker** 24+ và **Docker Compose** v2+
- **Git**

### Tài khoản cloud (cho tính năng đầy đủ)

- AWS Account với quyền truy cập: Cognito, S3, SES, SNS, SQS, EventBridge Scheduler
- FCM project (Firebase Cloud Messaging) cho Android push notification
- APNs certificate cho iOS push notification

---

## Cài đặt và chạy local

### Cách 1: Docker Compose (Khuyến nghị)

Đây là cách nhanh nhất để chạy toàn bộ stack bao gồm FastAPI và PostgreSQL.

```bash
# 1. Clone repository
git clone https://github.com/your-org/taskflow-api.git
cd taskflow-api

# 2. Tạo file .env từ template
cp .env.example .env

# 3. Chỉnh sửa .env với các giá trị phù hợp
# (xem phần Biến môi trường bên dưới)
nano .env

# 4. Khởi động services
docker compose up --build

# API sẽ chạy tại: http://localhost:8000
# Swagger UI:       http://localhost:8000/docs
# ReDoc:            http://localhost:8000/redoc
```

Để chạy ở background:

```bash
docker compose up -d --build

# Xem logs
docker compose logs -f api

# Dừng services
docker compose down

# Dừng và xóa volumes (reset database)
docker compose down -v
```

### Cách 2: Chạy trực tiếp với Python virtual environment

```bash
# 1. Clone repository
git clone https://github.com/your-org/taskflow-api.git
cd taskflow-api

# 2. Tạo và kích hoạt virtual environment
python3.11 -m venv venv
source venv/bin/activate        # Linux/macOS
# hoặc
venv\Scripts\activate           # Windows

# 3. Cài đặt dependencies
pip install -r requirements.txt

# 4. Tạo file .env
cp .env.example .env
# Chỉnh sửa .env với DATABASE_URL trỏ đến PostgreSQL local của bạn

# 5. Chạy database migrations
alembic upgrade head

# 6. Khởi động server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# API sẽ chạy tại: http://localhost:8000
```

### Chạy database migrations sau khi thay đổi models

```bash
# Tạo migration mới từ model changes
alembic revision --autogenerate -m "mô tả thay đổi"

# Áp dụng migrations
alembic upgrade head

# Rollback migration gần nhất
alembic downgrade -1

# Xem lịch sử migration
alembic history
```

---

## Biến môi trường

Sao chép file `.env.example` thành `.env` và điền các giá trị:

```bash
cp .env.example .env
```

### Các biến bắt buộc

| Biến | Mô tả | Ví dụ |
|------|-------|-------|
| `DATABASE_URL` | PostgreSQL connection string (async) | `postgresql+asyncpg://user:pass@localhost:5432/taskflow` |
| `SECRET_KEY` | Secret key cho JWT signing | Chuỗi random 64+ ký tự |
| `AWS_REGION` | AWS region | `ap-southeast-1` |
| `COGNITO_USER_POOL_ID` | Cognito User Pool ID | `ap-southeast-1_xxxxxxxxx` |
| `COGNITO_CLIENT_ID` | Cognito App Client ID | `xxxxxxxxxxxxxxxxxxxxxxxxxx` |

### Các biến tùy chọn (AWS Services)

| Biến | Mô tả | Mặc định |
|------|-------|---------|
| `AWS_ACCESS_KEY_ID` | AWS access key (dùng IAM role nếu trên EC2/ECS) | — |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | — |
| `S3_BUCKET_NAME` | S3 bucket lưu avatar | `taskflow-avatars` |
| `SES_SENDER_EMAIL` | Email gửi đi qua SES | `noreply@taskflow.app` |
| `SNS_PLATFORM_ARN_IOS` | SNS Platform Application ARN cho APNs | — |
| `SNS_PLATFORM_ARN_ANDROID` | SNS Platform Application ARN cho FCM | — |
| `SQS_NOTIFICATION_QUEUE_URL` | SQS queue URL cho notification buffer | — |
| `EVENTBRIDGE_SCHEDULER_ROLE_ARN` | IAM Role ARN cho EventBridge Scheduler | — |
| `FCM_SERVER_KEY` | Firebase Server Key cho FCM | — |

Xem chi tiết tất cả biến trong file `.env.example`.

---

## API Documentation

Sau khi khởi động server, truy cập:

| URL | Mô tả |
|-----|-------|
| `http://localhost:8000/docs` | **Swagger UI** — Interactive API documentation với Try it out |
| `http://localhost:8000/redoc` | **ReDoc** — Tài liệu API dạng đọc, dễ in ấn |
| `http://localhost:8000/openapi.json` | OpenAPI schema (JSON) |

### Tóm tắt các endpoint chính

#### Authentication (`/auth`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/auth/register` | Đăng ký tài khoản mới bằng email/password |
| `POST` | `/auth/verify-email` | Xác minh email với code từ Cognito |
| `POST` | `/auth/resend-verification` | Gửi lại email xác minh |
| `POST` | `/auth/login` | Đăng nhập, nhận JWT token |
| `POST` | `/auth/forgot-password` | Yêu cầu reset mật khẩu |
| `POST` | `/auth/reset-password` | Đặt mật khẩu mới với code xác nhận |

#### User Profile (`/users`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/users/profile` | Lấy thông tin profile của current user |
| `PUT` | `/users/profile` | Cập nhật tên hiển thị và các thuộc tính |
| `POST` | `/users/profile/avatar-upload-url` | Tạo S3 pre-signed URL để upload avatar |

#### Tasks (`/tasks`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/tasks` | Danh sách task với filter, sort, pagination |
| `POST` | `/tasks` | Tạo task mới |
| `GET` | `/tasks/{task_id}` | Xem chi tiết task |
| `PUT` | `/tasks/{task_id}` | Chỉnh sửa task |
| `DELETE` | `/tasks/{task_id}` | Soft-delete task (undo trong 5 giây) |
| `POST` | `/tasks/{task_id}/restore` | Hoàn tác xóa task |
| `PATCH` | `/tasks/{task_id}/toggle-complete` | Đánh dấu hoàn thành / bỏ đánh dấu |
| `GET` | `/tasks/search` | Tìm kiếm full-text theo tiêu đề và mô tả |
| `POST` | `/tasks/batch-overdue` | Trigger batch update trạng thái OVERDUE |

#### Categories (`/categories`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/categories` | Danh sách danh mục kèm số lượng task |
| `POST` | `/categories` | Tạo danh mục mới (tối đa 50) |
| `PUT` | `/categories/{category_id}` | Chỉnh sửa tên và màu danh mục |
| `DELETE` | `/categories/{category_id}` | Xóa danh mục, task chuyển sang Uncategorized |

#### Reminders (`/tasks/{task_id}/reminders`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/tasks/{task_id}/reminders` | Danh sách nhắc nhở của task |
| `POST` | `/tasks/{task_id}/reminders` | Tạo nhắc nhở (tối đa 3/task) |
| `PUT` | `/tasks/{task_id}/reminders/{reminder_id}` | Cập nhật nhắc nhở |
| `DELETE` | `/tasks/{task_id}/reminders/{reminder_id}` | Xóa nhắc nhở |
| `POST` | `/tasks/{task_id}/reminders/{reminder_id}/trigger` | Gửi thông báo thủ công |

#### Dashboard & Statistics (`/dashboard`, `/stats`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/dashboard` | Tổng quan: task hôm nay, sắp tới, quá hạn |
| `GET` | `/stats` | Thống kê cá nhân: tỷ lệ hoàn thành, streak |

### Authentication

Tất cả endpoint (trừ `/auth/*`) yêu cầu JWT Bearer token trong header:

```
Authorization: Bearer <access_token>
```

Token được lấy từ response của `POST /auth/login`.

---

## Cấu trúc thư mục

```
taskflow-api/
├── main.py                          # FastAPI entry point, CORS, router includes
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Multi-stage Docker build
├── docker-compose.yml               # Docker Compose cho local dev
├── .env.example                     # Template biến môi trường
├── alembic.ini                      # Cấu hình Alembic migrations
├── README.md                        # Tài liệu này
│
├── alembic/
│   ├── env.py                       # Alembic async environment config
│   └── versions/                    # Migration files
│
└── app/
    ├── config.py                    # Pydantic Settings, load env vars
    ├── database.py                  # SQLAlchemy async engine, session, Base
    ├── dependencies.py              # FastAPI Depends: get_db, get_current_user
    │
    ├── auth/
    │   ├── cognito.py               # AWS Cognito integration
    │   └── router.py                # Auth API routes
    │
    ├── models/
    │   ├── user.py                  # User, UserProfile SQLAlchemy models
    │   ├── task.py                  # Task, Category models + enums
    │   └── reminder.py              # Reminder model
    │
    ├── schemas/
    │   ├── user.py                  # Pydantic schemas cho user/profile
    │   ├── task.py                  # Pydantic schemas cho task
    │   ├── category.py              # Pydantic schemas cho category
    │   └── reminder.py              # Pydantic schemas cho reminder
    │
    ├── services/
    │   ├── user.py                  # User business logic, S3 pre-signed URL
    │   ├── task.py                  # Task CRUD, search, filter, overdue batch
    │   ├── category.py              # Category CRUD với giới hạn 50
    │   ├── reminder.py              # Reminder + EventBridge Scheduler
    │   ├── notification.py          # SNS push, SES email, delivery log
    │   └── dashboard.py             # Dashboard aggregation, streak calc
    │
    └── api/
        ├── user.py                  # User profile routes
        ├── task.py                  # Task CRUD routes
        ├── category.py              # Category routes
        ├── reminder.py              # Reminder routes
        └── dashboard.py             # Dashboard & stats routes
```

---

## Database Schema

### Các bảng chính

```
users
  ├── id (UUID, PK)
  ├── cognito_sub (VARCHAR, UNIQUE)
  ├── email (VARCHAR, UNIQUE)
  ├── display_name (VARCHAR)
  ├── avatar_url (VARCHAR, nullable)
  ├── is_active (BOOLEAN)
  └── created_at, updated_at

categories
  ├── id (UUID, PK)
  ├── user_id (FK → users)
  ├── name (VARCHAR 100)
  ├── color (VARCHAR 7, hex color)
  ├── is_default (BOOLEAN)  ← "Uncategorized"
  └── created_at, updated_at

tasks
  ├── id (UUID, PK)
  ├── user_id (FK → users)
  ├── category_id (FK → categories, nullable)
  ├── title (VARCHAR 255)
  ├── description (TEXT 5000, nullable)
  ├── status (ENUM: PENDING, OVERDUE, COMPLETED)
  ├── priority (ENUM: LOW, MEDIUM, HIGH, URGENT)
  ├── deadline (TIMESTAMPTZ, nullable)
  ├── completed_at (TIMESTAMPTZ, nullable)
  ├── deleted_at (TIMESTAMPTZ, nullable)  ← soft delete
  └── created_at, updated_at

reminders
  ├── id (UUID, PK)
  ├── task_id (FK → tasks)
  ├── user_id (FK → users)
  ├── reminder_type (ENUM: ONE_TIME, RECURRING)
  ├── channel (ENUM: PUSH, EMAIL, BOTH)
  ├── remind_at (TIMESTAMPTZ, nullable)   ← for one-time
  ├── cron_expression (VARCHAR, nullable) ← for recurring
  ├── eventbridge_schedule_name (VARCHAR, nullable)
  ├── is_active (BOOLEAN)
  └── created_at, updated_at
```

---

## Testing

```bash
# Cài đặt test dependencies
pip install pytest pytest-asyncio httpx

# Chạy tất cả tests
pytest

# Chạy với coverage report
pytest --cov=app --cov-report=html

# Chạy test cho module cụ thể
pytest tests/test_tasks.py -v

# Chạy integration tests
pytest tests/integration/ -v --asyncio-mode=auto
```

---

## Deployment

### Sử dụng Docker

```bash
# Build production image
docker build -t taskflow-api:latest .

# Chạy container
docker run -d \
  --name taskflow-api \
  -p 8000:8000 \
  --env-file .env \
  taskflow-api:latest
```

### AWS ECS / Fargate

1. Push Docker image lên ECR:
   ```bash
   aws ecr get-login-password --region ap-southeast-1 | \
     docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com
   
   docker tag taskflow-api:latest <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/taskflow-api:latest
   docker push <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com/taskflow-api:latest
   ```

2. Tạo ECS Task Definition với image URI từ ECR
3. Cấu hình ECS Service với Application Load Balancer
4. Sử dụng IAM Role cho Task (thay vì access key) để truy cập AWS services
5. Kết nối RDS PostgreSQL qua VPC private subnet

### Health Check

```
GET /health
→ {"status": "healthy", "version": "1.1.0"}
```

---

## Contributing

1. Fork repository
2. Tạo feature branch: `git checkout -b feature/ten-tinh-nang`
3. Commit changes: `git commit -m "feat: thêm tính năng X"`
4. Push branch: `git push origin feature/ten-tinh-nang`
5. Tạo Pull Request

### Commit Convention

Sử dụng [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — Tính năng mới
- `fix:` — Sửa lỗi
- `docs:` — Cập nhật tài liệu
- `refactor:` — Refactor code
- `test:` — Thêm/sửa tests
- `chore:` — Maintenance tasks

---

## License

MIT License — xem file [LICENSE](LICENSE) để biết thêm chi tiết.

---

## Liên hệ

- **Email:** dev@taskflow.app
- **Issues:** [GitHub Issues](https://github.com/your-org/taskflow-api/issues)
- **Wiki:** [GitHub Wiki](https://github.com/your-org/taskflow-api/wiki)