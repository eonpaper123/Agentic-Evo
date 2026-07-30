using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class AgenticEvoScmProbe
{
    private const int ErrorFailedServiceControllerConnect = 1063;
    private const int ErrorInvalidParameter = 87;
    private const int ServiceWin32OwnProcess = 0x00000010;
    private const int ServiceStartPending = 0x00000002;
    private const int ServiceStopPending = 0x00000003;
    private const int ServiceRunning = 0x00000004;
    private const int ServiceStopped = 0x00000001;
    private const int ServiceAcceptStop = 0x00000001;
    private const int ServiceAcceptShutdown = 0x00000004;
    private const int ServiceControlStop = 0x00000001;
    private const int ServiceControlShutdown = 0x00000005;

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate void ServiceMainFunction(int argumentCount, IntPtr arguments);

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate int ServiceControlHandler(
        int control,
        int eventType,
        IntPtr eventData,
        IntPtr context);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct ServiceTableEntry
    {
        [MarshalAs(UnmanagedType.LPWStr)]
        internal string ServiceName;
        internal ServiceMainFunction ServiceMain;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ServiceStatus
    {
        internal int ServiceType;
        internal int CurrentState;
        internal int ControlsAccepted;
        internal int Win32ExitCode;
        internal int ServiceSpecificExitCode;
        internal int CheckPoint;
        internal int WaitHint;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool StartServiceCtrlDispatcher(
        [In] ServiceTableEntry[] serviceTable);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr RegisterServiceCtrlHandlerEx(
        string serviceName,
        ServiceControlHandler handler,
        IntPtr context);

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetServiceStatus(
        IntPtr statusHandle,
        ref ServiceStatus serviceStatus);

    private static readonly ManualResetEvent StopRequested = new ManualResetEvent(false);
    private static readonly ServiceMainFunction ServiceMainRoot = ServiceMain;
    private static readonly ServiceControlHandler ControlHandlerRoot = HandleControl;
    private static string _serviceName = "AgenticEvoGateAProbe";
    private static string _probePath = null;
    private static IntPtr _statusHandle = IntPtr.Zero;

    private static int Main(string[] arguments)
    {
        try
        {
            if (arguments.Length == 1 && arguments[0] == "console-probe")
            {
                return RunDispatcher();
            }
            if (arguments.Length > 0 && arguments[0] == "service")
            {
                if (!ReadServiceArguments(arguments))
                {
                    return ErrorInvalidParameter;
                }
                return RunDispatcher();
            }
            return ErrorInvalidParameter;
        }
        catch
        {
            return 1;
        }
    }

    private static bool ReadServiceArguments(string[] arguments)
    {
        string serviceName = ReadOption(arguments, "--service-name");
        string probePath = ReadOption(arguments, "--probe-path");
        if (String.IsNullOrWhiteSpace(serviceName) ||
            String.IsNullOrWhiteSpace(probePath) ||
            serviceName.Length > 128 ||
            serviceName.IndexOfAny(new[] { '\\', '/', '\0' }) >= 0)
        {
            return false;
        }
        _serviceName = serviceName;
        _probePath = Path.GetFullPath(probePath);
        return true;
    }

    private static string ReadOption(string[] arguments, string name)
    {
        for (int index = 1; index + 1 < arguments.Length; index += 2)
        {
            if (arguments[index] == name)
            {
                return arguments[index + 1];
            }
        }
        return null;
    }

    private static int RunDispatcher()
    {
        ServiceTableEntry[] table = new ServiceTableEntry[2];
        table[0].ServiceName = _serviceName;
        table[0].ServiceMain = ServiceMainRoot;
        table[1].ServiceName = null;
        table[1].ServiceMain = null;
        if (StartServiceCtrlDispatcher(table))
        {
            return 0;
        }
        int error = Marshal.GetLastWin32Error();
        return error == 0 ? 1 : error;
    }

    private static void ServiceMain(int argumentCount, IntPtr arguments)
    {
        _statusHandle = RegisterServiceCtrlHandlerEx(
            _serviceName,
            ControlHandlerRoot,
            IntPtr.Zero);
        if (_statusHandle == IntPtr.Zero)
        {
            return;
        }

        ReportStatus(ServiceStartPending, 0, 1, 5000);
        try
        {
            WriteProbeRecord();
            ReportStatus(
                ServiceRunning,
                ServiceAcceptStop | ServiceAcceptShutdown,
                0,
                0);
            StopRequested.WaitOne();
            ReportStatus(ServiceStopPending, 0, 1, 5000);
            ReportStatus(ServiceStopped, 0, 0, 0);
        }
        catch
        {
            ReportStatus(ServiceStopped, 0, 0, 0, 1);
        }
    }

    private static int HandleControl(
        int control,
        int eventType,
        IntPtr eventData,
        IntPtr context)
    {
        if (control == ServiceControlStop || control == ServiceControlShutdown)
        {
            StopRequested.Set();
        }
        return 0;
    }

    private static void ReportStatus(
        int state,
        int acceptedControls,
        int checkPoint,
        int waitHint,
        int exitCode = 0)
    {
        ServiceStatus status = new ServiceStatus();
        status.ServiceType = ServiceWin32OwnProcess;
        status.CurrentState = state;
        status.ControlsAccepted = acceptedControls;
        status.Win32ExitCode = exitCode;
        status.ServiceSpecificExitCode = 0;
        status.CheckPoint = checkPoint;
        status.WaitHint = waitHint;
        SetServiceStatus(_statusHandle, ref status);
    }

    private static void WriteProbeRecord()
    {
        string directory = Path.GetDirectoryName(_probePath);
        if (String.IsNullOrEmpty(directory) || !Directory.Exists(directory))
        {
            throw new DirectoryNotFoundException();
        }
        string payload =
            "{\"kind\":\"scm_witness_write_probe\",\"pid\":" +
            System.Diagnostics.Process.GetCurrentProcess().Id.ToString() +
            "}\n";
        byte[] bytes = new UTF8Encoding(false).GetBytes(payload);
        using (FileStream stream = new FileStream(
            _probePath,
            FileMode.Create,
            FileAccess.Write,
            FileShare.Read,
            4096,
            FileOptions.WriteThrough))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }
    }

}
