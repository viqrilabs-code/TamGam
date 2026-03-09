from dataclasses import dataclass
from uuid import UUID, uuid4

from app.api.v1.endpoints.subscriptions import list_plans


@dataclass
class _FakePlan:
    id: UUID
    name: str
    slug: str
    price_monthly_paise: int
    price_annual_paise: int
    description: str
    features: object
    is_active: bool = True


class _FakeQuery:
    def __init__(self, plans):
        self._plans = plans

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._plans


class _FakeDB:
    def __init__(self, plans):
        self._plans = plans

    def query(self, _model):
        return _FakeQuery(self._plans)


def test_list_plans_hides_subjects_allowed_and_parses_features():
    db = _FakeDB(
        [
            _FakePlan(
                id=uuid4(),
                name="Learn Core",
                slug="core",
                price_monthly_paise=69900,
                price_annual_paise=699900,
                description="Core plan",
                features='["Diya questions: 120/month"]',
            ),
            _FakePlan(
                id=uuid4(),
                name="Learn Plus",
                slug="plus",
                price_monthly_paise=129900,
                price_annual_paise=1299900,
                description="Plus plan",
                features=["Diya questions: 500/month"],
            ),
        ]
    )

    rows = list_plans(db=db)
    assert len(rows) == 2

    first = rows[0].model_dump()
    second = rows[1].model_dump()

    assert "subjects_allowed" not in first
    assert "subjects_allowed" not in second
    assert first["features"] == ["Diya questions: 120/month"]
    assert second["features"] == ["Diya questions: 500/month"]
