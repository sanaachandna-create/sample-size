name: Update standings

on:
  workflow_dispatch:
    inputs:
      season:
        description: "Season start year, e.g. 2025 (blank = auto)"
        required: false
  schedule:
    - cron: "17 9 * * *"   # once a day

permissions:
  contents: write

jobs:
  standings:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Compute standings
        env:
          SEASON: ${{ github.event.inputs.season }}
          BDL_KEY: ${{ secrets.BDL_KEY }}
        run: python standings.py
      - name: Commit the data if it changed
        run: |
          git config user.name "sample-size-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data/standings.json
          git diff --staged --quiet || git commit -m "standings: update $(date -u +%FT%TZ)"
          git push
