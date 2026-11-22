using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using LibreHardwareMonitor.Hardware;

namespace WattWatchBridge
{
    public class UpdateVisitor : IVisitor
    {
        public void VisitComputer(IComputer computer)
        {
            if (computer != null)
            {
                computer.Traverse(this);
            }
        }

        public void VisitHardware(IHardware hardware)
        {
            if (hardware == null) return;
            try
            {
                hardware.Update();
            }
            catch
            {
            }
            try
            {
                foreach (IHardware subHardware in hardware.SubHardware)
                {
                    subHardware.Accept(this);
                }
            }
            catch
            {
            }
        }

        public void VisitSensor(ISensor sensor) { }
        public void VisitParameter(IParameter parameter) { }
    }

    internal static class Program
    {
        private static volatile bool _running = true;
        private static volatile int _intervalMs = 1000;

        [DllImport("kernel32.dll")]
        private static extern bool SetProcessWorkingSetSize(IntPtr proc, IntPtr min, IntPtr max);

        private static void TrimMemory()
        {
            try
            {
                GC.Collect(GC.MaxGeneration, GCCollectionMode.Forced, true);
                GC.WaitForPendingFinalizers();
                SetProcessWorkingSetSize(Process.GetCurrentProcess().Handle, (IntPtr)(-1), (IntPtr)(-1));
            }
            catch
            {
            }
        }

        private static bool TryValidFloat(float? val, out double result)
        {
            result = 0.0;
            if (!val.HasValue) return false;
            float f = val.Value;
            if (float.IsNaN(f) || float.IsInfinity(f)) return false;
            result = Math.Round((double)f, 3);
            return true;
        }

        private static void CollectSubHardware(IHardware hw, List<IHardware> list)
        {
            try
            {
                foreach (IHardware sub in hw.SubHardware)
                {
                    list.Add(sub);
                    CollectSubHardware(sub, list);
                }
            }
            catch
            {
            }
        }

        private static void StdinWatcher()
        {
            try
            {
                while (_running)
                {
                    string line = Console.In.ReadLine();
                    if (line == null)
                    {
                        _running = false;
                        break;
                    }
                    string trimmed = line.Trim();
                    if (trimmed.Equals("quit", StringComparison.OrdinalIgnoreCase))
                    {
                        _running = false;
                        break;
                    }
                    if (trimmed.Equals("gc", StringComparison.OrdinalIgnoreCase))
                    {
                        TrimMemory();
                    }
                    if (trimmed.StartsWith("interval ", StringComparison.OrdinalIgnoreCase))
                    {
                        int ms;
                        if (int.TryParse(trimmed.Substring(9).Trim(), out ms))
                        {
                            if (ms < 1000) ms = 1000;
                            if (ms > 10000) ms = 10000;
                            _intervalMs = ms;
                        }
                    }
                }
            }
            catch
            {
                _running = false;
            }
        }

        private static void Main(string[] args)
        {
            Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
            try
            {
                Process.GetCurrentProcess().PriorityClass = ProcessPriorityClass.BelowNormal;
            }
            catch
            {
            }

            Thread watcher = new Thread(StdinWatcher);
            watcher.IsBackground = true;
            watcher.Start();

            Computer computer = new Computer();
            computer.IsCpuEnabled = true;
            computer.IsGpuEnabled = true;
            computer.IsMemoryEnabled = false;
            computer.IsMotherboardEnabled = false;
            computer.IsStorageEnabled = false;
            computer.IsBatteryEnabled = true;

            try
            {
                computer.Open();
            }
            catch
            {
            }

            UpdateVisitor visitor = new UpdateVisitor();
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            bool didInitialTrim = false;

            while (_running)
            {
                try
                {
                    computer.Accept(visitor);
                }
                catch
                {
                }
                if (!didInitialTrim)
                {
                    didInitialTrim = true;
                    TrimMemory();
                }

                double? cpuPowerW = null;
                double cpuFallbackPowerSum = 0.0;
                bool hasCpuFallbackPower = false;
                double? dramPowerW = null;
                double? cpuTempC = null;
                List<double> cpuClocks = new List<double>();

                List<Dictionary<string, object>> gpus = new List<Dictionary<string, object>>();
                List<Dictionary<string, object>> fans = new List<Dictionary<string, object>>();
                List<Dictionary<string, object>> storageTemps = new List<Dictionary<string, object>>();

                double? batDischargeW = null;
                double? batChargeW = null;
                double? batLevelPct = null;
                bool hasBattery = false;

                int powerSensorCount = 0;

                try
                {
                    foreach (IHardware hw in computer.Hardware)
                    {
                        List<IHardware> allHw = new List<IHardware>();
                        allHw.Add(hw);
                        CollectSubHardware(hw, allHw);

                        if (hw.HardwareType == HardwareType.Cpu)
                        {
                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    string sName = s.Name ?? "";

                                    if (s.SensorType == SensorType.Power)
                                    {
                                        if (sName.IndexOf("DRAM", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            sName.IndexOf("Memory", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            if (v > 0.1)
                                            {
                                                powerSensorCount++;
                                                dramPowerW = v;
                                            }
                                        }
                                        else if (sName.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            if (v > 0.5)
                                            {
                                                powerSensorCount++;
                                                cpuPowerW = v;
                                            }
                                        }
                                        else if (sName.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                                 sName.IndexOf("SoC", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                                 sName.IndexOf("PPT", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            if (v > 0.5)
                                            {
                                                powerSensorCount++;
                                                cpuFallbackPowerSum += v;
                                                hasCpuFallbackPower = true;
                                            }
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Temperature)
                                    {
                                        if (sName.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            sName.IndexOf("Tctl", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            sName.IndexOf("Tdie", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            !cpuTempC.HasValue)
                                        {
                                            cpuTempC = v;
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Clock)
                                    {
                                        if (sName.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0 && v > 100.0)
                                        {
                                            cpuClocks.Add(v);
                                        }
                                    }
                                }
                            }
                        }
                        else if (hw.HardwareType == HardwareType.GpuNvidia ||
                                 hw.HardwareType == HardwareType.GpuAmd ||
                                 hw.HardwareType == HardwareType.GpuIntel)
                        {
                            double? gPower = null;
                            double? gTemp = null;
                            double? gLoad = null;
                            double? gFan = null;

                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    string sName = s.Name ?? "";

                                    if (s.SensorType == SensorType.Power)
                                    {
                                        powerSensorCount++;
                                        if (!gPower.HasValue ||
                                            sName.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            sName.IndexOf("Total", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                            sName.Equals("GPU Power", StringComparison.OrdinalIgnoreCase))
                                        {
                                            gPower = v;
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Temperature)
                                    {
                                        if (!gTemp.HasValue || sName.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            gTemp = v;
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Load)
                                    {
                                        if (sName.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            gLoad = v;
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Fan)
                                    {
                                        if (!gFan.HasValue) gFan = v;
                                    }
                                }
                            }

                            Dictionary<string, object> gpuObj = new Dictionary<string, object>();
                            gpuObj["name"] = hw.Name;
                            gpuObj["type"] = hw.HardwareType.ToString();
                            gpuObj["power_w"] = gPower;
                            gpuObj["temp_c"] = gTemp;
                            gpuObj["load_pct"] = gLoad;
                            gpuObj["fan_rpm"] = gFan;
                            gpus.Add(gpuObj);
                        }
                        else if (hw.HardwareType == HardwareType.Memory)
                        {
                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    if (s.SensorType == SensorType.Power)
                                    {
                                        powerSensorCount++;
                                        if (!dramPowerW.HasValue) dramPowerW = v;
                                    }
                                }
                            }
                        }
                        else if (hw.HardwareType == HardwareType.Motherboard ||
                                 hw.HardwareType == HardwareType.SuperIO ||
                                 hw.HardwareType == HardwareType.Cooler ||
                                 hw.HardwareType == HardwareType.EmbeddedController)
                        {
                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    if (s.SensorType == SensorType.Fan && v > 50.0)
                                    {
                                        Dictionary<string, object> fanObj = new Dictionary<string, object>();
                                        fanObj["name"] = s.Name;
                                        fanObj["rpm"] = v;
                                        fans.Add(fanObj);
                                    }
                                }
                            }
                        }
                        else if (hw.HardwareType == HardwareType.Storage)
                        {
                            double? sTemp = null;
                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    if (s.SensorType == SensorType.Temperature && !sTemp.HasValue)
                                    {
                                        sTemp = v;
                                    }
                                }
                            }
                            if (sTemp.HasValue)
                            {
                                Dictionary<string, object> stObj = new Dictionary<string, object>();
                                stObj["name"] = hw.Name;
                                stObj["temp_c"] = sTemp.Value;
                                storageTemps.Add(stObj);
                            }
                        }
                        else if (hw.HardwareType == HardwareType.Battery)
                        {
                            hasBattery = true;
                            foreach (IHardware item in allHw)
                            {
                                foreach (ISensor s in item.Sensors)
                                {
                                    double v;
                                    if (!TryValidFloat(s.Value, out v)) continue;
                                    string sName = s.Name ?? "";
                                    if (s.SensorType == SensorType.Power)
                                    {
                                        powerSensorCount++;
                                        if (sName.IndexOf("Discharge", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            batDischargeW = v;
                                        }
                                        else if (sName.IndexOf("Charge", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            batChargeW = v;
                                        }
                                    }
                                    else if (s.SensorType == SensorType.Level)
                                    {
                                        if (sName.IndexOf("Charge", StringComparison.OrdinalIgnoreCase) >= 0)
                                        {
                                            batLevelPct = v;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                catch
                {
                }

                if (!cpuPowerW.HasValue && hasCpuFallbackPower)
                {
                    cpuPowerW = Math.Round(cpuFallbackPowerSum, 3);
                }

                double? avgClock = null;
                if (cpuClocks.Count > 0)
                {
                    double sum = 0.0;
                    for (int i = 0; i < cpuClocks.Count; i++) sum += cpuClocks[i];
                    avgClock = Math.Round(sum / cpuClocks.Count, 1);
                }

                Dictionary<string, object> batteryObj = null;
                if (hasBattery)
                {
                    batteryObj = new Dictionary<string, object>();
                    batteryObj["discharge_w"] = batDischargeW;
                    batteryObj["charge_w"] = batChargeW;
                    batteryObj["level_pct"] = batLevelPct;
                }

                long unixTs = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

                Dictionary<string, object> payload = new Dictionary<string, object>();
                payload["ts"] = unixTs;
                payload["cpu_power_w"] = cpuPowerW;
                payload["dram_power_w"] = dramPowerW;
                payload["cpu_temp_c"] = cpuTempC;
                payload["cpu_clock_mhz"] = avgClock;
                payload["gpus"] = gpus;
                payload["fans"] = fans;
                payload["storage_temps"] = storageTemps;
                payload["battery"] = batteryObj;
                payload["sensor_count"] = powerSensorCount;

                try
                {
                    string json = serializer.Serialize(payload);
                    Console.WriteLine(json);
                    Console.Out.Flush();
                }
                catch
                {
                }

                int slept = 0;
                while (slept < _intervalMs && _running)
                {
                    Thread.Sleep(100);
                    slept += 100;
                }
            }

            try
            {
                computer.Close();
            }
            catch
            {
            }
        }
    }
}
