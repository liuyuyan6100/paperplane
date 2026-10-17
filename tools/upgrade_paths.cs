using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class UpgradePaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool QueryFullProcessImageName(IntPtr h, uint flags, StringBuilder path, ref uint size);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle h, StringBuilder path, uint size, uint flags);
    public static string Image(IntPtr handle) {
        var text = new StringBuilder(32768); uint n = (uint)text.Capacity;
        if(!QueryFullProcessImageName(handle, 0, text, ref n)) throw new IOException("Cannot resolve process path");
        return text.ToString();
    }
    public static string Final(string path) {
        using(var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) {
            var text = new StringBuilder(32768);
            uint n = GetFinalPathNameByHandle(file.SafeFileHandle, text, (uint)text.Capacity, 0);
            if(n == 0 || n >= text.Capacity) throw new IOException("Cannot resolve file path");
            return text.ToString();
        }
    }
}
