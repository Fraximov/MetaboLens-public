from app.db.session import SessionLocal
from app.services.seed_service import SeedService


if __name__ == "__main__":
    with SessionLocal() as db:
        SeedService(db).run()
    print("Seed completed")
