import pytest

from bot.db.models import Base, User
from bot.db.session import create_engine, create_sessionmaker
from tests.harness import make_harness


@pytest.fixture
async def session():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with create_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def user(session):
    user = User(tg_id=1, timezone="Europe/Moscow", base_currency="USD")
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def h(tmp_path):
    harness, engine = await make_harness(tmp_path / "t.db")
    yield harness
    await engine.dispose()
