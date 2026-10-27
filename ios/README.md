# Quenya for iOS

Native SwiftUI client for Quenya. The app targets iOS 17 and talks to the
production API by default.

## Generate, build and test

Install XcodeGen, then generate the committed Xcode project:

```sh
brew install xcodegen
cd ios
xcodegen generate
xcodebuild -project Quenya.xcodeproj -scheme Quenya \
  -destination 'platform=iOS Simulator,name=iPhone 17' build
xcodebuild -project Quenya.xcodeproj -scheme Quenya \
  -destination 'platform=iOS Simulator,name=iPhone 17' test
```

## Local API

The local backend helper is a development-only fake. It replaces the LLM
entry points with deterministic schema-valid responses and selects the tag
ranker so it does not require model embeddings.

From the repository root, prepare a local database and catalogue:

```sh
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
PYTHON_DOTENV_DISABLED=1 DB_PATH=/tmp/q.sqlite \
  .venv/bin/python ../ios/scripts/seed_local_catalog.py
```

Create local invites and save the printed invite URLs outside the repository:

```sh
cd backend
QUENYA_APP_URL=http://localhost:8000 .venv/bin/python -m app.accounts bootstrap-admin --name Admin
QUENYA_APP_URL=http://localhost:8000 .venv/bin/python -m app.accounts invite --name T
```

Run the fake backend:

```sh
cd backend
PYTHON_DOTENV_DISABLED=1 DB_PATH=/tmp/q.sqlite QUENYA_WARM_CACHES=0 \
  .venv/bin/python ../ios/scripts/dev_backend.py
```

In Xcode, pass `-QuenyaAPIBaseURL http://localhost:8000` to the app launch
arguments. The same override can be used with `simctl`:

```sh
xcrun simctl launch booted uk.co.quenya.app \
  -QuenyaAPIBaseURL http://localhost:8000
```

The app's default URL is production. Always use the local override when
launching against a local or test backend.
