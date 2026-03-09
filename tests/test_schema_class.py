import pytest
from pydantic import ValidationError

from app.schemas.class_ import BatchCreateRequest


def test_batch_create_request_allows_grade_5_to_10():
    low = BatchCreateRequest(name="A", grade_level=5)
    high = BatchCreateRequest(name="B", grade_level=10)

    assert low.grade_level == 5
    assert high.grade_level == 10


@pytest.mark.parametrize("grade_level", [4, 11])
def test_batch_create_request_rejects_out_of_range_grade(grade_level):
    with pytest.raises(ValidationError, match="Grade level must be one of: 5, 6, 7, 8, 9, 10"):
        BatchCreateRequest(name="A", grade_level=grade_level)
