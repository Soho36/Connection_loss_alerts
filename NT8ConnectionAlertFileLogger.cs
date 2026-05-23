#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class NT8ConnectionAlertFileLogger : Indicator
    {
        private Dictionary<string, bool> priceConnectionLostByConnection;
        private Dictionary<string, bool> orderConnectionLostByConnection;

        [NinjaScriptProperty]
        [Display(Name = "Enable File Alerts", Order = 0, GroupName = "Connection Alert File Queue")]
        public bool EnableFileAlerts { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Alert Queue File Path", Order = 1, GroupName = "Connection Alert File Queue")]
        public string AlertQueueFilePath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Log Price Connection Loss", Order = 2, GroupName = "Connection Alert File Queue")]
        public bool LogPriceConnectionLoss { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Log Order Connection Loss", Order = 3, GroupName = "Connection Alert File Queue")]
        public bool LogOrderConnectionLoss { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Log Reconnect Events", Order = 4, GroupName = "Connection Alert File Queue")]
        public bool LogReconnectEvents { get; set; }

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "NT8ConnectionAlertFileLogger";
                Description = "Writes NinjaTrader connection loss alerts to a file queue for an external notifier.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                DrawOnPricePanel = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;

                EnableFileAlerts = true;
                AlertQueueFilePath = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    "NinjaTrader 8",
                    "connection_alerts",
                    "connection_alert_queue.tsv");
                LogPriceConnectionLoss = true;
                LogOrderConnectionLoss = true;
                LogReconnectEvents = false;
            }
            else if (State == State.DataLoaded)
            {
                priceConnectionLostByConnection = new Dictionary<string, bool>();
                orderConnectionLostByConnection = new Dictionary<string, bool>();
            }
        }

        protected override void OnBarUpdate()
        {
        }

        protected override void OnConnectionStatusUpdate(ConnectionStatusEventArgs connectionStatusUpdate)
        {
            if (State != State.Realtime)
                return;

            string connectionName = GetConnectionName(connectionStatusUpdate);

            if (LogPriceConnectionLoss)
                HandleConnectionSide("PRICE", connectionName, connectionStatusUpdate.PriceStatus, connectionStatusUpdate);

            if (LogOrderConnectionLoss)
                HandleConnectionSide("ORDER", connectionName, connectionStatusUpdate.Status, connectionStatusUpdate);
        }

        private void HandleConnectionSide(string connectionType, string connectionName, ConnectionStatus status, ConnectionStatusEventArgs connectionStatusUpdate)
        {
            Dictionary<string, bool> stateByConnection = connectionType == "PRICE"
                ? priceConnectionLostByConnection
                : orderConnectionLostByConnection;

            bool wasLost = stateByConnection.ContainsKey(connectionName) && stateByConnection[connectionName];

            if (status == ConnectionStatus.ConnectionLost && !wasLost)
            {
                stateByConnection[connectionName] = true;
                WriteConnectionAlertToFile(connectionType, connectionStatusUpdate);
            }
            else if (status == ConnectionStatus.Connected && wasLost)
            {
                stateByConnection[connectionName] = false;

                if (LogReconnectEvents)
                    WriteConnectionAlertToFile(connectionType + "_RECONNECTED", connectionStatusUpdate);
            }
        }

        private void WriteConnectionAlertToFile(string connectionType, ConnectionStatusEventArgs connectionStatusUpdate)
        {
            DateTime now = DateTime.Now;

            if (!EnableFileAlerts || string.IsNullOrWhiteSpace(AlertQueueFilePath))
                return;

            try
            {
                string directory = Path.GetDirectoryName(AlertQueueFilePath);

                if (!string.IsNullOrWhiteSpace(directory))
                    Directory.CreateDirectory(directory);

                if (!File.Exists(AlertQueueFilePath))
                {
                    File.AppendAllText(
                        AlertQueueFilePath,
                        "id\tcreated_at\tconnection_type\tstrategy\tinstrument\taccount\tconnection\torder_status\tprevious_order_status\tprice_status\tprevious_price_status\tposition\tposition_quantity\ttracked_order\tnative_error" + Environment.NewLine);
                }

                string connectionName = GetConnectionName(connectionStatusUpdate);
                string instrumentName = Instrument != null ? Instrument.FullName : "No chart instrument";
                string alertId = string.Format("{0}-{1}-{2}-{3}",
                    now.ToString("yyyyMMddHHmmssfff"),
                    Name,
                    connectionName,
                    connectionType);

                string[] fields = new string[]
                {
                    alertId,
                    now.ToString("yyyy-MM-dd HH:mm:ss"),
                    connectionType,
                    Name,
                    instrumentName,
                    "All accounts",
                    connectionName,
                    connectionStatusUpdate.Status.ToString(),
                    connectionStatusUpdate.PreviousStatus.ToString(),
                    connectionStatusUpdate.PriceStatus.ToString(),
                    connectionStatusUpdate.PreviousPriceStatus.ToString(),
                    "Unknown",
                    "0",
                    "Managed by standalone alert logger, no trading order tracked",
                    connectionStatusUpdate.NativeError
                };

                File.AppendAllText(AlertQueueFilePath, string.Join("\t", EscapeAlertFields(fields)) + Environment.NewLine);
                Print($"[{now}] {connectionType} connection alert written to file queue: {AlertQueueFilePath}");
            }
            catch (Exception ex)
            {
                Print($"[{now}] Failed to write {connectionType} connection alert to file queue. Error: {ex.Message}");
            }
        }

        private string GetConnectionName(ConnectionStatusEventArgs connectionStatusUpdate)
        {
            return connectionStatusUpdate.Connection != null && connectionStatusUpdate.Connection.Options != null
                ? connectionStatusUpdate.Connection.Options.Name
                : "Unknown";
        }

        private string[] EscapeAlertFields(string[] fields)
        {
            string[] escaped = new string[fields.Length];

            for (int i = 0; i < fields.Length; i++)
            {
                escaped[i] = (fields[i] ?? string.Empty)
                    .Replace("\\", "\\\\")
                    .Replace("\t", "\\t")
                    .Replace("\r", "\\r")
                    .Replace("\n", "\\n");
            }

            return escaped;
        }
    }
}
