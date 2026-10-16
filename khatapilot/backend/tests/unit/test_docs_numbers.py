"""Every number in the README and the docs comes from the result files (scripts/update_docs.py), never by hand.
If this fails, run `python scripts/update_docs.py` and commit the docs with the results that changed them."""

from scripts.update_docs import update


def test_the_numbers_in_the_docs_match_the_result_files():
    # The test counts come from the run this test is part of, so they are refreshed after it, not checked here.
    assert update(check=True, skip=("tests.",)) == []
