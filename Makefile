PYTHON ?= python3

.PHONY: setup sample test clean
setup:
	$(PYTHON) -m pip install -r requirements.txt

sample:
	$(PYTHON) baseline_naive_rag.py \
		--questions eval/sample_questions.json \
		--round-id sample \
		--output outputs/submission_sample.json \
		--rebuild

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf outputs/qdrant outputs/submission_*.json .pytest_cache __pycache__ tests/__pycache__
