# Crypto Control Desktop

Minimal WinForms shell around the existing local FastAPI interface.

## Requirements

- Windows x64
- .NET 9 Desktop Runtime (or SDK for building)
- Microsoft Edge WebView2 Runtime
- Crypto Control Engine available on `http://127.0.0.1:8000`

The shell does not start a second engine and does not open Chrome. Closing its window leaves the background engine and Task Scheduler untouched. WebView2 navigation is restricted to the local engine URL. Its profile is stored under `%LOCALAPPDATA%\CryptoControl\WebView2` and is not part of the repository.

## Build

From the repository root:

```powershell
dotnet publish .\desktop\CryptoControl.Desktop\CryptoControl.Desktop.csproj -c Release --self-contained false
```

The test executable is produced at:

`desktop\CryptoControl.Desktop\bin\Release\net9.0-windows\publish\CryptoControl.Desktop.exe`

Keep the published executable and its companion files together. Start the engine with `.\start.cmd` in a separate terminal, then launch the executable. If the engine is unavailable, the window offers a retry.

## Optional background engine

After `start.cmd` has prepared the virtual environment, close the foreground server. From the repository root:

```powershell
.\engine.ps1 -Action install
.\engine.ps1 -Action start
.\engine.ps1 -Action status
```

Installation registers a Task Scheduler job for the current user. Use `-Action stop` to stop the scheduled engine and `-Action start` to clear the stop marker and resume. Use `-Action uninstall` to remove the scheduled job. Run one engine at a time.

## Tests

Install the test-only dependencies into the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Then run the test suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

Run from the repository root. Windows engine integration tests require loopback sockets and exercise isolated temporary databases. To smoke-test Desktop manually, start the engine, open the application, check the connection, then close the window and check engine status again.
