namespace CryptoControl.Desktop;

static class Program
{
    /// <summary>
    ///  The main entry point for the application.
    /// </summary>
    [STAThread]
    static int Main(string[] args)
    {
        // To customize application configuration such as set high DPI settings or default font,
        // see https://aka.ms/applicationconfiguration.
        ApplicationConfiguration.Initialize();
        int? testPort = null;
        if (args.Length != 0)
        {
            if (args.Length != 2 || args[0] != "--test-port"
                || !int.TryParse(args[1], out int port) || port is < 1024 or > 65535 || port == 8000)
                return 2;
            testPort = port;
        }
        Application.Run(new Form1(testPort));
        return 0;
    }    
}
