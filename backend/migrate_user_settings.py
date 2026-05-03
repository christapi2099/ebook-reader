"""Migration script to add UserSettings table to the database."""
import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from sqlmodel import SQLModel, create_engine
from db.models import UserSettings

def migrate():
    """Create the UserSettings table if it doesn't exist."""
    # Use the same database path as the main app
    db_path = backend_dir / "ebook_reader.db"
    
    if not db_path.exists():
        print(f"Database file not found: {db_path}")
        return False
    
    engine = create_engine(f"sqlite:///{db_path}")
    
    # Check if UserSettings table exists
    from sqlalchemy import inspect
    inspector = inspect(engine)
    
    if "usersettings" in inspector.get_table_names():
        print("UserSettings table already exists. Skipping migration.")
        return True
    
    # Create the table
    SQLModel.metadata.create_all(engine, tables=[UserSettings.__table__])
    print("UserSettings table created successfully.")
    
    return True

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
