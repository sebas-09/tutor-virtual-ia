using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using OpenMetaverse;

class Program
{
    // ===================== CONFIG =====================
    class AppSettings
    {
        public SecondLifeSettings SecondLife { get; set; } = new();
        public string OrchestratorApi { get; set; } = "http://127.0.0.1:7000";
        public int ChunkDelayMs { get; set; } = 900; // recomendado para LOCAL chat
        public bool AutoStreamAllChunks { get; set; } = true;
        public bool RespondOnlyWhenMentioned { get; set; } = false;
    }

    class SecondLifeSettings
    {
        public string FirstName { get; set; } = "";
        public string LastName { get; set; } = "";
        public string Password { get; set; } = "";
        public string LoginUri { get; set; } = "https://login.agni.lindenlab.com/cgi-bin/login.cgi";
        public string StartLocation { get; set; } = "uri:Warbunton&30&17&36";
    }

    static AppSettings Settings = LoadSettings();

    static string FIRST_NAME => Settings.SecondLife.FirstName;
    static string LAST_NAME => Settings.SecondLife.LastName;
    static string PASSWORD => Settings.SecondLife.Password;
    static string LOGIN_URI => Settings.SecondLife.LoginUri;
    static string START_LOCATION => Settings.SecondLife.StartLocation;
    static string ORCH_API => Settings.OrchestratorApi;

    // ===================== CLIENTES =====================
    static readonly GridClient Client = new GridClient();
    static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
    static readonly JsonSerializerOptions JsonOpts = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };

    // ===================== ESTADO =====================
    static readonly ConcurrentDictionary<string, SemaphoreSlim> UserLocks = new();
    static readonly ConcurrentDictionary<string, DateTime> LastReplyAtUtcByUser = new();
    static readonly ConcurrentDictionary<string, string> LastMsgByUser = new();
    static readonly ConcurrentDictionary<string, string> UserPreferredMode = new(); // "class" | "info"
    static readonly ConcurrentDictionary<string, bool> UserGreeted = new();

    // para logs
    static readonly ConcurrentDictionary<UUID, string> NameByUser = new();

    static readonly TimeSpan PerUserCooldown = TimeSpan.FromSeconds(2.0);
    static Timer? KeepAliveTimer;

    // ===================== MODO -> COORDENADAS =====================
    static readonly string TARGET_REGION = "Warbunton";
    static readonly Vector3 POS_INFO = new Vector3(22f, 2f, 36f);
    static readonly Vector3 POS_CLASS = new Vector3(30f, 13f, 36f);

    // ===================== ANIMACIONES (UUID-only) =====================
    static readonly object AnimLock = new();
    static UUID? CurrentAnimId = null;

    // ===================== OUTBOUND ORDERING =====================
    // Cola única por medio para garantizar orden de envío y evitar ráfagas / reorden visual en SL.
    enum Medium { Local, IM }

    record OutMsg(Medium Medium, UUID To, string Text);

    static readonly ConcurrentQueue<OutMsg> OutQueue = new();
    static readonly SemaphoreSlim OutSignal = new(0);
    static readonly SemaphoreSlim LocalSendGate = new(1, 1);
    static readonly SemaphoreSlim ImSendGate = new(1, 1);

    static CancellationTokenSource OutCts = new();

    // ===================== MAIN =====================
    static async Task<int> Main(string[] args)
    {
        ValidateConfig();

        Client.Settings.MULTIPLE_SIMS = false;
        Client.Settings.SEND_AGENT_UPDATES = true;
        Client.Throttle.Total = 600000.0f;

        Client.Network.LoginProgress += Network_LoginProgress;
        Client.Network.Disconnected += Network_Disconnected;
        Client.Network.SimConnected += Network_SimConnected;
        Client.Network.SimDisconnected += Network_SimDisconnected;

        Client.Self.IM += Self_IM;
        Client.Self.ChatFromSimulator += Self_ChatFromSimulator;

        Console.WriteLine("Iniciando login...");

        var lp = Client.Network.DefaultLoginParams(
            FIRST_NAME, LAST_NAME, PASSWORD, "TutorBot/1.5", "tutor@example.com");

        lp.URI = LOGIN_URI;
        lp.Start = START_LOCATION;

        if (!Client.Network.Login(lp))
        {
            Console.WriteLine("❌ Error login: " + Client.Network.LoginMessage);
            return 1;
        }

        // worker de salida (1 solo escritor por medio)
        _ = Task.Run(() => OutboundWorkerAsync(OutCts.Token));

        Console.WriteLine("✅ Login OK. Esperando mensajes. Ctrl+C para salir.");
        var greeting = "Tutor en línea";
        await ReplyAsync("local", Client.Self.AgentID, greeting); // local (to no se usa)
        KeepAliveTimer = new Timer(_ => tryKeepAlive(), null, TimeSpan.FromMinutes(4), TimeSpan.FromMinutes(4));

        Console.CancelKeyPress += (s, e) =>
        {
            e.Cancel = true;
            try { OutCts.Cancel(); } catch { }
            Environment.Exit(0);
        };

        while (true) await Task.Delay(1000);
    }

    // ===================== CONFIG HELPERS =====================
    static AppSettings LoadSettings()
    {
        var opts = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        var baseDir = AppContext.BaseDirectory;
        var currDir = Directory.GetCurrentDirectory();
        var paths = new[]
        {
            Path.Combine(baseDir, "appsettings.json"),
            Path.Combine(currDir, "appsettings.json")
        };

        foreach (var path in paths)
        {
            if (!File.Exists(path)) continue;
            try
            {
                Console.WriteLine($"⚙️ Cargando configuración: {path}");
                var json = File.ReadAllText(path);
                return JsonSerializer.Deserialize<AppSettings>(json, opts) ?? new AppSettings();
            }
            catch (Exception ex)
            {
                Console.WriteLine($"⚠️ Error leyendo {path}: {ex.Message}");
            }
        }

        Console.WriteLine("⚠️ No se encontró appsettings.json, usando valores por defecto.");
        return new AppSettings();
    }

    static void ValidateConfig()
    {
        if (string.IsNullOrWhiteSpace(FIRST_NAME) ||
            string.IsNullOrWhiteSpace(LAST_NAME) ||
            string.IsNullOrWhiteSpace(PASSWORD))
        {
            Console.WriteLine("⛔ Config incompleta en appsettings.json -> SecondLife: FirstName/LastName/Password");
            Environment.Exit(2);
        }
    }

    // ===================== CHAT LOCAL =====================
    private static void Self_ChatFromSimulator(object? sender, ChatEventArgs chat)
    {
        try
        {
            if (chat.SourceType == ChatSourceType.Agent && chat.SourceID != Client.Self.AgentID)
            {
                var msg = (chat.Message ?? "").Trim();
                if (string.IsNullOrEmpty(msg)) return;
                if (msg == "..." || msg == "…") return;

                if (Settings.RespondOnlyWhenMentioned && !IsMentionToTutor(msg)) return;

                _ = HandleChat(chat.FromName, chat.SourceID, msg, medium: "local");
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("[ChatFromSimulator] " + ex.Message);
        }
    }

    // ===================== IM =====================
    private static void Self_IM(object? sender, InstantMessageEventArgs e)
    {
        if (e.IM.FromAgentID == Client.Self.AgentID) return;

        var msg = (e.IM.Message ?? "").Trim();
        if (string.IsNullOrEmpty(msg)) return;
        if (msg == "..." || msg == "…") return;

        _ = HandleChat(e.IM.FromAgentName, e.IM.FromAgentID, msg, medium: "im");
    }

    // ===================== HANDLE CHAT =====================
    private static async Task HandleChat(string fromName, UUID fromId, string msg, string medium)
    {
        if (string.IsNullOrWhiteSpace(msg)) return;

        Console.WriteLine($"[{medium.ToUpper()}] {fromName}: {msg}");
        NameByUser[fromId] = fromName;

        // 🔀 Cambio de modo explícito
        var modeSwitch = DetectModeSwitch(msg);
        if (modeSwitch != null)
        {
            var sessionId2 = fromId.ToString();
            UserPreferredMode[sessionId2] = modeSwitch;

            Console.WriteLine($"[MODE] Switch -> {modeSwitch}");

            ApplyModeEffects(modeSwitch);

            await ReplyAsync(medium, fromId,
                modeSwitch == "class"
                    ? "Modo clases activado. Dime el tema."
                    : "Modo información activado. ¿Qué quieres saber?");
            return;
        }

        // Comandos "!"
        if (IsCommand(msg, out var cmd, out var args))
        {
            await HandleCommand(fromId, cmd, args, medium);
            return;
        }

        var sessionId = fromId.ToString();
        var gate = LockFor(sessionId);
        if (!await gate.WaitAsync(0)) return;

        try
        {
            if (IsDuplicateMsg(sessionId, msg)) return;
            if (IsRateLimited(sessionId, PerUserCooldown)) return;

            // 1) Saludo inicial
            if (!GetOrDefault(UserGreeted, sessionId, false))
            {
                await ReplyAsync(medium, fromId, "Hola. Di “clases” para una clase guiada o “información” para respuestas rápidas.");
                UserGreeted[sessionId] = true;
                return;
            }

            // 2) Si no hay modo fijado, infiere
            var mode = GetOrDefault<string>(UserPreferredMode, sessionId, null);
            if (string.IsNullOrWhiteSpace(mode))
            {
                var low = Normalize(msg);

                if (low.Contains("clase") || low.Contains("practicar") || low.Contains("ejercic"))
                {
                    UserPreferredMode[sessionId] = "class";
                    ApplyModeEffects("class");
                    await ReplyAsync(medium, fromId, "Modo clases activado. Cuéntame tu tema o duda.");
                    return;
                }

                if (low.Contains("info") || low.Contains("informacion"))
                {
                    UserPreferredMode[sessionId] = "info";
                    ApplyModeEffects("info");
                    await ReplyAsync(medium, fromId, "Modo información activado. ¿Qué quieres saber?");
                    return;
                }

                await ReplyAsync(medium, fromId, "¿Prefieres “clases” o “información”?");
                return;
            }

            // 3) Llamada al orquestador
            var (ok, reply, chunks, waiting) = await OrchestratorChat(sessionId, fromName, msg, mode);

            if (!ok)
            {
                if (!string.IsNullOrWhiteSpace(reply))
                    await ReplyAsync(medium, fromId, reply);
                return;
            }

            // 4) Enviar chunks EN ORDEN, SIN re-trocear (evita doble chunking)
            if (chunks != null && chunks.Length > 0)
            {
                Console.WriteLine($"[ORCH] Respuesta con {chunks.Length} chunk(s).");

                foreach (var c in chunks)
                {
                    if (string.IsNullOrWhiteSpace(c)) continue;
                    await SendRawAsync(medium, fromId, c);                 // <- raw: no SoftWrap
                    await Task.Delay(Settings.ChunkDelayMs);              // <- un solo delay (aquí)
                }
            }
            else if (!string.IsNullOrWhiteSpace(reply))
            {
                // fallback: si vino un texto único, aquí sí usamos SoftWrap
                await ReplyAsync(medium, fromId, reply);
            }

            if (mode == "info")
                await ReplyAsync(medium, fromId, "¿Te ayudo con algo más o quieres cambiar a modo clases?");

            MarkReplied(sessionId);
            if (!waiting) await Task.Delay(600);
        }
        finally
        {
            gate.Release();
        }
    }

    // ===================== EFECTOS (MODO) =====================
    private static void ApplyModeEffects(string mode)
    {
        PlayModeAnimation(mode);

        if (mode == "class")
            MoveTutorTo(TARGET_REGION, POS_CLASS);
        else
            MoveTutorTo(TARGET_REGION, POS_INFO);
    }

    // ===== Animaciones por UUID (compatibles con tu build) =====
    private static UUID? TryGetAnimationUuid(string name)
    {
        try
        {
            var t = typeof(Animations);

            var fld = t.GetFields(BindingFlags.Public | BindingFlags.Static)
                       .FirstOrDefault(f =>
                           f.Name.Equals(name, StringComparison.OrdinalIgnoreCase) &&
                           f.FieldType == typeof(UUID));

            if (fld != null)
                return (UUID)fld.GetValue(null)!;
        }
        catch { }

        return null;
    }

    private static void PlayModeAnimation(string mode)
    {
        string[] candidates = (mode == "class")
            ? new[] { "YES", "CLAP", "BOW", "NO" }
            : new[] { "YES", "BOW", "CLAP", "NO" };

        UUID? animId = null;
        foreach (var c in candidates)
        {
            animId = TryGetAnimationUuid(c);
            if (animId.HasValue) break;
        }

        if (!animId.HasValue)
        {
            Console.WriteLine("[ANIM] No encontré Animations.<NAME> como UUID en esta build.");
            return;
        }

        try
        {
            lock (AnimLock)
            {
                if (CurrentAnimId.HasValue)
                {
                    try { Client.Self.AnimationStop(CurrentAnimId.Value, false); } catch { }
                }

                Client.Self.AnimationStart(animId.Value, false);
                CurrentAnimId = animId.Value;
            }

            Console.WriteLine($"[ANIM] AnimationStart(UUID={animId.Value})");

            _ = Task.Run(async () =>
            {
                await Task.Delay(1200);
                try
                {
                    lock (AnimLock)
                    {
                        if (CurrentAnimId.HasValue && CurrentAnimId.Value == animId.Value)
                        {
                            Client.Self.AnimationStop(animId.Value, false);
                            CurrentAnimId = null;
                        }
                    }
                    Console.WriteLine($"[ANIM] AnimationStop(UUID={animId.Value})");
                }
                catch { }
            });
        }
        catch (Exception ex)
        {
            Console.WriteLine("[ANIM] ERROR: " + ex.Message);
        }
    }

    // ===== Movimiento: AutoPilot adaptable + watchdog fallback Teleport =====
    private static void MoveTutorTo(string regionName, Vector3 localPos)
    {
        try
        {
            var sim = Client.Network.CurrentSim;

            var before = GetMyPos();
            Console.WriteLine($"[MOVE] CurrentSim={(sim?.Name ?? "NULL")} MyPos={before} Target={regionName} {localPos}");

            TryStandUp();

            // Si no está en la región -> Teleport directo
            if (sim == null || !string.Equals(sim.Name, regionName, StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine($"[MOVE] Teleport(region) -> {regionName} {localPos}");
                Client.Self.Teleport(regionName, localPos);
                return;
            }

            // AutoPilot (por reflexión, según firma real disponible)
            Console.WriteLine($"[MOVE] Intentando AutoPilot -> {localPos}");
            InvokeAutoPilot(localPos);

            // Watchdog: si no llegó cerca en 3.5s, fallback a Teleport
            _ = Task.Run(async () =>
            {
                await Task.Delay(3500);

                var after = GetMyPos();
                var d = Dist(after, localPos);
                Console.WriteLine($"[MOVE] Watchdog: MyPos={after} DistToTarget={d:0.00}");

                if (d > 2.5f)
                {
                    try
                    {
                        Console.WriteLine($"[MOVE] Fallback Teleport -> {regionName} {localPos}");
                        Client.Self.Teleport(regionName, localPos);
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine("[MOVE] Fallback Teleport ERROR: " + ex.Message);
                    }
                }
            });
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MOVE] ERROR: " + ex.Message);
        }
    }

    private static void InvokeAutoPilot(Vector3 target)
    {
        try
        {
            var self = Client.Self;
            var t = self.GetType();

            var methods = t.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                           .Where(m => m.Name == "AutoPilot")
                           .ToArray();

            if (methods.Length == 0)
            {
                Console.WriteLine("[MOVE] No existe AutoPilot() en esta build. Se usará Teleport por watchdog.");
                return;
            }

            // 1) AutoPilot(Vector3, bool)
            var m2 = methods.FirstOrDefault(m =>
            {
                var p = m.GetParameters();
                return p.Length == 2 && p[0].ParameterType == typeof(Vector3) && p[1].ParameterType == typeof(bool);
            });
            if (m2 != null)
            {
                m2.Invoke(self, new object[] { target, false }); // fly=false
                Console.WriteLine("[MOVE] AutoPilot(Vector3,bool) invocado.");
                return;
            }

            // 2) AutoPilot(Vector3, bool, bool)
            var m3 = methods.FirstOrDefault(m =>
            {
                var p = m.GetParameters();
                return p.Length == 3 &&
                       p[0].ParameterType == typeof(Vector3) &&
                       p[1].ParameterType == typeof(bool) &&
                       p[2].ParameterType == typeof(bool);
            });
            if (m3 != null)
            {
                m3.Invoke(self, new object[] { target, true, false }); // (stopAtEnd?, fly=false)
                Console.WriteLine("[MOVE] AutoPilot(Vector3,bool,bool) invocado.");
                return;
            }

            // 3) AutoPilot(Vector3)
            var m1 = methods.FirstOrDefault(m =>
            {
                var p = m.GetParameters();
                return p.Length == 1 && p[0].ParameterType == typeof(Vector3);
            });
            if (m1 != null)
            {
                m1.Invoke(self, new object[] { target });
                Console.WriteLine("[MOVE] AutoPilot(Vector3) invocado.");
                return;
            }

            Console.WriteLine("[MOVE] No encontré sobrecarga compatible de AutoPilot. Se usará Teleport por watchdog.");
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MOVE] InvokeAutoPilot ERROR: " + ex.Message);
        }
    }

    // ===== Posición / distancia / stand =====
    private static Vector3 GetMyPos()
    {
        try { return Client.Self.SimPosition; } catch { return Vector3.Zero; }
    }

    private static float Dist(Vector3 a, Vector3 b)
    {
        var dx = a.X - b.X;
        var dy = a.Y - b.Y;
        var dz = a.Z - b.Z;
        return (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
    }

    private static void TryStandUp()
    {
        try
        {
            var m = Client.Self.GetType().GetMethod("Stand", BindingFlags.Instance | BindingFlags.Public);
            if (m != null)
            {
                m.Invoke(Client.Self, null);
                Console.WriteLine("[MOVE] Stand() invocado.");
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("[MOVE] Stand() fallo: " + ex.Message);
        }
    }

    // ===================== COMANDOS =====================
    private static bool IsCommand(string msg, out string cmd, out string args)
    {
        cmd = ""; args = "";
        if (string.IsNullOrWhiteSpace(msg)) return false;

        if (msg.StartsWith("!"))
        {
            var parts = msg.Split(' ', 2);
            cmd = parts[0][1..].Trim();
            args = parts.Length > 1 ? parts[1].Trim() : "";
            return true;
        }
        return false;
    }

    private static async Task HandleCommand(UUID fromId, string cmd, string args, string medium)
    {
        var sessionId = fromId.ToString();
        switch (cmd.ToLowerInvariant())
        {
            case "modo":
                var a = Normalize((args ?? "").Trim());
                if (a == "class" || a == "clases")
                {
                    UserPreferredMode[sessionId] = "class";
                    ApplyModeEffects("class");
                    await ReplyAsync(medium, fromId, "Modo fijado a clases.");
                }
                else if (a == "info" || a == "informacion" || a == "información")
                {
                    UserPreferredMode[sessionId] = "info";
                    ApplyModeEffects("info");
                    await ReplyAsync(medium, fromId, "Modo fijado a información.");
                }
                else await ReplyAsync(medium, fromId, "Uso: !modo clases | info");
                break;

            default:
                await ReplyAsync(medium, fromId, "Comando no reconocido. Usa: !modo clases | info");
                break;
        }
    }

    // ===================== ENVÍO =====================
    // 1) SoftWrap para textos “normales” (internos del bot / fallback)
    private static IEnumerable<string> SoftWrap(string text, int maxBytes = 800)
    {
        if (string.IsNullOrWhiteSpace(text))
            yield break;

        text = text.Replace("\r\n", "\n").Trim();
        var utf8 = Encoding.UTF8;

        foreach (var rawLine in text.Split('\n'))
        {
            var s = rawLine.Trim();
            if (s.Length == 0) continue;

            int start = 0;
            while (start < s.Length)
            {
                int end = start;
                int lastSoftCut = -1;
                int byteCount = 0;

                while (end < s.Length)
                {
                    char ch = s[end];
                    int cb = utf8.GetByteCount(new[] { ch });
                    if (byteCount + cb > maxBytes)
                        break;

                    byteCount += cb;

                    if (char.IsWhiteSpace(ch) || ch == '.' || ch == ',' || ch == ';' ||
                        ch == ':' || ch == '!' || ch == '?')
                        lastSoftCut = end;

                    end++;
                }

                if (end == start) end++;

                int cutPos = (lastSoftCut >= start) ? (lastSoftCut + 1) : end;
                string piece = s.Substring(start, cutPos - start).Trim();
                if (piece.Length > 0) yield return piece;

                start = cutPos;
                while (start < s.Length && char.IsWhiteSpace(s[start])) start++;
            }
        }
    }

    private static string GetDisplayName(UUID id)
        => NameByUser.TryGetValue(id, out var n) && !string.IsNullOrWhiteSpace(n) ? n : id.ToString();

    // 2) ReplyAsync: usa SoftWrap y encola cada piece en la cola de salida (sin Thread.Sleep)
    private static async Task ReplyAsync(string medium, UUID to, string text)
    {
        foreach (var piece in SoftWrap(text, 800))
        {
            await SendRawAsync(medium, to, piece);
            await Task.Delay(Settings.ChunkDelayMs);
        }
    }

    // 3) SendRawAsync: NO parte el texto (ideal para chunks del orquestador)
    private static Task SendRawAsync(string medium, UUID to, string piece)
    {
        var m = (medium == "im") ? Medium.IM : Medium.Local;
        var outMsg = new OutMsg(m, to, piece);
        OutQueue.Enqueue(outMsg);
        OutSignal.Release();
        return Task.CompletedTask;
    }

    // 4) Worker: único punto que hace Client.Self.Chat / InstantMessage (garantiza orden)
    private static async Task OutboundWorkerAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                await OutSignal.WaitAsync(ct);
                if (!OutQueue.TryDequeue(out var msg)) continue;

                if (msg.Medium == Medium.Local)
                {
                    await LocalSendGate.WaitAsync(ct);
                    try
                    {
                        Console.WriteLine($"[LOCAL] TUTOR → cerca (local): {msg.Text}");
                        Client.Self.Chat(msg.Text, 0, ChatType.Normal);
                    }
                    finally { LocalSendGate.Release(); }
                }
                else
                {
                    await ImSendGate.WaitAsync(ct);
                    try
                    {
                        var toWho = GetDisplayName(msg.To);
                        Console.WriteLine($"[IM] TUTOR → {toWho}: {msg.Text}");
                        Client.Self.InstantMessage(msg.To, msg.Text);
                    }
                    finally { ImSendGate.Release(); }
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                Console.WriteLine("[OUTBOUND] ERROR: " + ex.Message);
            }
        }
    }

    // ===================== ORQUESTADOR =====================
    private record OrchestratorResp(
        bool ok,
        string routed_to,
        string answer,
        string[] chunks,
        bool? degraded,
        int? retry_after,
        string error);

    private static async Task<(bool ok, string reply, string[] chunks, bool waiting)> OrchestratorChat(
        string sessionId, string userName, string message, string mode)
    {
        var payload = new
        {
            mode = string.IsNullOrWhiteSpace(mode) ? "class" : mode,
            message,
            session_id = sessionId,
            channel = "sl",
            user_name = userName
        };

        for (int attempt = 0; attempt < 4; attempt++)
        {
            try
            {
                var res = await PostJsonAsync($"{ORCH_API}/route", payload);
                var body = await res.Content.ReadAsStringAsync();

                if ((int)res.StatusCode == 429)
                {
                    try
                    {
                        var dto429 = JsonSerializer.Deserialize<OrchestratorResp>(body, JsonOpts);
                        var ra = dto429?.retry_after ?? 2;
                        await Task.Delay(TimeSpan.FromSeconds(Math.Min(ra + attempt, 6)));
                        continue;
                    }
                    catch
                    {
                        await Task.Delay(500 + 300 * attempt);
                        continue;
                    }
                }

                if ((int)res.StatusCode >= 500)
                {
                    try
                    {
                        var dto5 = JsonSerializer.Deserialize<OrchestratorResp>(body, JsonOpts);
                        var msg = dto5?.answer ?? "(Servicio temporalmente degradado)";
                        return (false, msg, Array.Empty<string>(), true);
                    }
                    catch
                    {
                        await Task.Delay(600 + 300 * attempt);
                        continue;
                    }
                }

                if (!res.IsSuccessStatusCode)
                {
                    Console.WriteLine("Orchestrator /route falló: " + body);
                    return (false, "(hubo un problema con el orquestador)", Array.Empty<string>(), true);
                }

                var dto = JsonSerializer.Deserialize<OrchestratorResp>(body, JsonOpts);

                // OJO: aquí respetamos chunks del orquestador (ya vienen listos para SL)
                var outChunks = (dto?.chunks != null && dto.chunks.Length > 0)
                    ? dto!.chunks
                    : (string.IsNullOrWhiteSpace(dto?.answer) ? Array.Empty<string>() : new[] { dto!.answer });

                string first = (outChunks.Length > 0) ? outChunks[0] : (dto?.answer ?? "");
                return (dto?.ok ?? false, first, outChunks, true);
            }
            catch (TaskCanceledException)
            {
                await Task.Delay(350 + 300 * attempt);
            }
            catch (Exception ex)
            {
                Console.WriteLine("OrchestratorChat error: " + ex.Message);
                await Task.Delay(350 + 300 * attempt);
            }
        }

        return (false, "(error de conexión con el orquestador)", Array.Empty<string>(), true);
    }

    private static async Task<HttpResponseMessage> PostJsonAsync(string url, object payload, CancellationToken ct = default)
    {
        var json = JsonSerializer.Serialize(payload);
        using var content = new StringContent(json, Encoding.UTF8, "application/json");

        var req = new HttpRequestMessage(HttpMethod.Post, url);
        req.Headers.ConnectionClose = false;
        req.Content = content;

        return await Http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
    }

    // ===================== UTILIDADES =====================
    private static SemaphoreSlim LockFor(string sessionId)
        => UserLocks.GetOrAdd(sessionId, _ => new SemaphoreSlim(1, 1));

    private static bool IsRateLimited(string sessionId, TimeSpan window)
    {
        var last = GetOrDefault(LastReplyAtUtcByUser, sessionId, DateTime.MinValue);
        return (DateTime.UtcNow - last) < window;
    }

    private static void MarkReplied(string sessionId)
        => LastReplyAtUtcByUser[sessionId] = DateTime.UtcNow;

    private static bool IsDuplicateMsg(string sessionId, string msg)
    {
        var prev = GetOrDefault(LastMsgByUser, sessionId, (string)null);
        LastMsgByUser[sessionId] = msg;
        return prev != null && string.Equals(prev, msg, StringComparison.OrdinalIgnoreCase);
    }

    private static T GetOrDefault<T>(ConcurrentDictionary<string, T> dict, string key, T defaultValue)
        => dict.TryGetValue(key, out var val) ? val! : defaultValue;

    private static bool IsMentionToTutor(string msg)
    {
        var t = Normalize((msg ?? "").Trim());
        return t.StartsWith("tutor ") || t.StartsWith("@tutor");
    }

    private static string? DetectModeSwitch(string msg)
    {
        var t = Normalize((msg ?? "").Trim());

        if (t.Contains("clases") || t.Contains("modo clase") || t.Contains("modo: clase"))
            return "class";

        if (t.Contains("informacion") || t.Contains("info") || t.Contains("modo info") || t.Contains("modo: info"))
            return "info";

        return null;
    }

    private static string Normalize(string s)
    {
        var t = (s ?? "").Trim().ToLowerInvariant();
        return t.Replace("á", "a").Replace("é", "e").Replace("í", "i").Replace("ó", "o").Replace("ú", "u");
    }

    private static void tryKeepAlive()
    {
        try { Client.Self.Movement.SendUpdate(true); } catch { }
    }

    // ===================== EVENTOS =====================
    private static void Network_LoginProgress(object? sender, LoginProgressEventArgs e)
        => Console.WriteLine($"[LOGIN] {e.Status} - {e.Message}");

    private static void Network_Disconnected(object? sender, DisconnectedEventArgs e)
        => Console.WriteLine($"🔌 Desconectado: {e.Reason}");

    private static void Network_SimConnected(object? sender, SimConnectedEventArgs e)
        => Console.WriteLine($"🌐 Conectado a simulador: {e.Simulator.Name}");

    private static void Network_SimDisconnected(object? sender, SimDisconnectedEventArgs e)
        => Console.WriteLine($"🛑 Sim desconectado: {e.Simulator?.Name}");
}
