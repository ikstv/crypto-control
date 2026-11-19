namespace CryptoControl.Desktop;

using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

public partial class Form1 : Form
{
    private const string EngineUrl = "http://127.0.0.1:8000/";
    private readonly WebView2 browser = new();
    private readonly Label status = new();
    private readonly Button retry = new();

    public Form1()
    {
        InitializeComponent();
        Text = "Crypto Control Desktop";
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
                || uri.Port != 8000)
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
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
        try
        {
            using var response = await client.GetAsync("http://127.0.0.1:8000/api/v1/status");
            response.EnsureSuccessStatusCode();
            status.Text = "Crypto Control Engine підключено";
            var dataPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "CryptoControl", "WebView2");
            Directory.CreateDirectory(dataPath);
            await browser.EnsureCoreWebView2Async(
                await CoreWebView2Environment.CreateAsync(null, dataPath));
            browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
            browser.CoreWebView2.Navigate(EngineUrl);
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException
                                      or WebView2RuntimeNotFoundException)
        {
            status.Text = "Фонове ядро недоступне. Запустіть його та повторіть підключення.";
            retry.Visible = true;
        }
    }
}
