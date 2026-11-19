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

Start the engine first with `.\engine.ps1 -Action start`, then launch the executable. If the engine is unavailable, the window shows a retry message.
