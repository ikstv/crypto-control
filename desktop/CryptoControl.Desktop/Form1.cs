namespace CryptoControl.Desktop;

using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

public partial class Form1 : Form
{
    private readonly Uri engineUrl;
    private readonly string dataPath;
    private readonly bool isolated;
    private readonly WebView2 browser = new();
    private readonly Label status = new();
    private readonly Button retry = new();

    public Form1(int? testPort = null)
    {
        isolated = testPort.HasValue;
        engineUrl = new Uri($"http://127.0.0.1:{testPort ?? 8000}/");
        dataPath = isolated
            ? Path.Combine(Path.GetTempPath(), "CryptoControl-Test", Guid.NewGuid().ToString("N"))
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "CryptoControl", "WebView2");
        InitializeComponent();
        Text = isolated ? $"Crypto Control Desktop · тестовий порт {testPort}" : "Crypto Control Desktop";
        Icon = SystemIcons.Application;
        browser.Dock = DockStyle.Fill;
        status.Dock = DockStyle.Top;
        status.Height = 42;
        status.Padding = new Padding(12, 10, 12, 8);
        retry.Text = "Повторити підключення";
        retry.Dock = DockStyle.Top;
        retry.Height = 36;
        retry.Visible = false;
        retry.Click += async (_, _) => await ConnectAsync();
        browser.NavigationStarting += (_, args) =>
        {
            if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri)
                || uri.Scheme != Uri.UriSchemeHttp
                || uri.Host != "127.0.0.1"
                || uri.Port != engineUrl.Port
                || uri.UserInfo.Length != 0)
            {
                args.Cancel = true;
            }
        };
        Controls.Add(browser);
        Controls.Add(retry);
        Controls.Add(status);
        Load += async (_, _) => await ConnectAsync();
    }

    private async Task ConnectAsync()
    {
        retry.Visible = false;
        status.Text = "Підключення до Crypto Control Engine…";
        using var handler = new HttpClientHandler { AllowAutoRedirect = false, UseProxy = false };
        using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(2) };
        try
        {
            using var response = await client.GetAsync(new Uri(engineUrl, "api/v1/status"));
            response.EnsureSuccessStatusCode();
            status.Text = "Crypto Control Engine підключено";
            Directory.CreateDirectory(dataPath);
            var core = browser.CoreWebView2;
            if (core is null)
            {
                await browser.EnsureCoreWebView2Async(
                    await CoreWebView2Environment.CreateAsync(null, dataPath));
                core = browser.CoreWebView2 ?? throw new InvalidOperationException("WebView2 initialization failed.");
                var environment = core.Environment;
                core.NewWindowRequested += (_, args) => args.Handled = true;
                core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                core.WebResourceRequested += (_, args) =>
                {
                    if (!Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var uri)
                        || uri.Scheme != engineUrl.Scheme || uri.Host != engineUrl.Host
                        || uri.Port != engineUrl.Port || uri.UserInfo.Length != 0)
                        args.Response = environment.CreateWebResourceResponse(
                            null, 403, "Blocked", "");
                };
            }
            core.Settings.AreDevToolsEnabled = false;
            core.Navigate(engineUrl.AbsoluteUri);
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException
                                      or WebView2RuntimeNotFoundException)
        {
            status.Text = isolated
                ? $"Тестове ядро на порту {engineUrl.Port} недоступне."
                : "Фонове ядро недоступне. Запустіть його та повторіть підключення.";
            retry.Visible = true;
        }
    }
}
