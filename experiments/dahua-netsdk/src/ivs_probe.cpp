// Read-only capability probe: does a Dahua camera deliver live per-frame IVS
// data (object boxes and camera-local track IDs) inside its real-time stream?
//
// Data path:
//   NetSDK real-play (raw private stream)
//     -> PlaySDK demux/decode (no window)
//     -> PLAY_SetIVSCallBack
//
// The PlaySDK headers shipped with this SDK package document the IVS callback
// but do not define the binary object structures it refers to
// (SP_IVS_OBJ_EX, SP_IVS_COMMON_OBJ). Binary payloads are therefore stored raw
// (hex) for offline analysis; only JSON payloads are stored as text. Nothing
// here changes camera configuration.

#include <windows.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "dhnetsdk.h"

namespace fs = std::filesystem;

namespace {

constexpr std::size_t kMaxPayloadBytes = 256 * 1024;
constexpr std::size_t kMaxQueuedRecords = 10000;
constexpr std::uint64_t kMaxWrittenPayloadBytes = 512ull * 1024 * 1024;
constexpr DWORD kPlayBufferBytes = 3 * 1024 * 1024;
constexpr DWORD kStreamRealtime = 0;  // STREAME_REALTIME in PlaySDK play.h
constexpr LONG kTrackRecordType = 7;  // IVSINFOTYPE_TRACK_EX_B0: live targets

std::mutex g_console_mutex;

// Stop request shared by the stdin reader, the timer and the stall watchdog.
// Global so a detached stdin reader never outlives the state it touches.
std::mutex g_stop_mutex;
std::condition_variable g_stop_condition;
bool g_stop_requested = false;
int g_exit_code = 0;

void request_stop(int exit_code) {
    {
        std::lock_guard<std::mutex> lock(g_stop_mutex);
        if (g_stop_requested) return;
        g_stop_requested = true;
        g_exit_code = exit_code;
    }
    g_stop_condition.notify_all();
}

// ---------------------------------------------------------------------------
// Small helpers (kept local so the existing collector stays untouched).

template <std::size_t N>
std::string fixed_string(const char (&value)[N]) {
    const auto end = std::find(value, value + N, '\0');
    return std::string(value, end);
}

std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return {};
    }
    const auto last = value.find_last_not_of(" \t\r\n");
    value = value.substr(first, last - first + 1);
    if (value.size() >= 2 &&
        ((value.front() == '"' && value.back() == '"') ||
         (value.front() == '\'' && value.back() == '\''))) {
        value = value.substr(1, value.size() - 2);
    }
    return value;
}

std::map<std::string, std::string> load_env(const fs::path& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("Cannot open environment file: " + path.string());
    }
    std::map<std::string, std::string> result;
    std::string line;
    while (std::getline(input, line)) {
        line = trim(line);
        if (line.empty() || line.front() == '#') {
            continue;
        }
        const auto separator = line.find('=');
        if (separator == std::string::npos) {
            throw std::runtime_error("Malformed line in environment file (missing '=')");
        }
        result[trim(line.substr(0, separator))] = trim(line.substr(separator + 1));
    }
    return result;
}

std::map<std::string, std::string> load_process_env() {
    std::map<std::string, std::string> result;
    for (const char* key : {"DAHUA_HOST", "DAHUA_PORT", "DAHUA_USER", "DAHUA_PASSWORD"}) {
        char* value = nullptr;
        std::size_t length = 0;
        if (_dupenv_s(&value, &length, key) == 0 && value != nullptr) {
            result[key] = value;
        }
        std::free(value);
    }
    return result;
}

const std::string& require_env(
    const std::map<std::string, std::string>& values,
    const std::string& key) {
    const auto it = values.find(key);
    if (it == values.end() || it->second.empty()) {
        throw std::runtime_error("Missing required environment variable: " + key);
    }
    return it->second;
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(ch) << std::dec;
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

std::string format_utc_time(const std::chrono::system_clock::time_point& value) {
    const auto seconds = std::chrono::time_point_cast<std::chrono::seconds>(value);
    const auto milliseconds =
        std::chrono::duration_cast<std::chrono::milliseconds>(value - seconds).count();
    const std::time_t raw_time = std::chrono::system_clock::to_time_t(value);
    std::tm utc{};
    gmtime_s(&utc, &raw_time);
    std::ostringstream out;
    out << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S") << '.'
        << std::setfill('0') << std::setw(3) << milliseconds << 'Z';
    return out.str();
}

std::string compact_utc_stamp(const std::chrono::system_clock::time_point& value) {
    const std::time_t raw_time = std::chrono::system_clock::to_time_t(value);
    std::tm utc{};
    gmtime_s(&utc, &raw_time);
    std::ostringstream out;
    out << std::put_time(&utc, "%Y%m%dT%H%M%SZ");
    return out.str();
}

std::string to_hex(const std::vector<unsigned char>& data) {
    static constexpr char kDigits[] = "0123456789abcdef";
    std::string out;
    out.reserve(data.size() * 2);
    for (const unsigned char byte : data) {
        out.push_back(kDigits[byte >> 4]);
        out.push_back(kDigits[byte & 0x0F]);
    }
    return out;
}

bool is_valid_utf8_text(const std::vector<unsigned char>& data) {
    std::size_t i = 0;
    while (i < data.size()) {
        const unsigned char c = data[i];
        if (c == 0) {
            // Trailing NUL terminators are common in C string payloads.
            return std::all_of(data.begin() + static_cast<std::ptrdiff_t>(i), data.end(),
                               [](unsigned char b) { return b == 0; });
        }
        std::size_t extra = 0;
        if (c < 0x80) {
            extra = 0;
        } else if ((c & 0xE0) == 0xC0) {
            extra = 1;
        } else if ((c & 0xF0) == 0xE0) {
            extra = 2;
        } else if ((c & 0xF8) == 0xF0) {
            extra = 3;
        } else {
            return false;
        }
        if (extra > 0 && i + extra >= data.size()) {
            return false;  // Sequence cut short, e.g. by payload truncation.
        }
        for (std::size_t k = 1; k <= extra; ++k) {
            if ((data[i + k] & 0xC0) != 0x80) {
                return false;
            }
        }
        i += extra + 1;
    }
    return true;
}

// IVS_TYPE values from the PlaySDK header shipped with the SDK demos.
std::string ivs_type_name(LONG type) {
    switch (type) {
        case 1: return "PRESETPOS";
        case 2: return "MOTINTRKS";
        case 3: return "MOTINTRKS_EX";
        case 4: return "LIGHT";
        case 5: return "RAWDATA_JSON";
        case 6: return "TRACK";
        case 7: return "TRACK_EX_B0";
        case 9: return "MOTIONFRAME";
        case 10: return "VIDEO_CONCENTRATION";
        case 11: return "OVERLAY_PIC";
        case 12: return "OSD_INFO";
        case 13: return "GPS_INFO";
        case 14: return "TAGGING_INFO";
        case 15: return "TRACK_A1";
        case 16: return "DATA_WITH_LARGE_AMOUNT";
        case 17: return "TRACK_A1_EX";
        case 18: return "WATER_LEVEL_MONITOR";
        case 19: return "INTELFLOW";
        case 20: return "SOUND_DECIBEL";
        case 21: return "SMART_MOTION";
        default: return "UNKNOWN";
    }
}

std::string sdk_error() {
    std::ostringstream out;
    out << "0x" << std::hex << std::uppercase << CLIENT_GetLastError();
    return out.str();
}

// ---------------------------------------------------------------------------
// PlaySDK, loaded dynamically. This SDK package ships play.dll but no import
// library; the signatures below are copied from its PlaySDK header (play.h).

using PlayIvsCallback = void(__stdcall*)(
    char* buffer, LONG type, LONG length, LONG frame_sequence, void* reserved, void* user);

struct PlaySdk {
    HMODULE module{};
    BOOL(__stdcall* get_free_port)(LONG*) {};
    BOOL(__stdcall* release_port)(LONG) {};
    BOOL(__stdcall* set_stream_open_mode)(LONG, DWORD) {};
    BOOL(__stdcall* open_stream)(LONG, PBYTE, DWORD, DWORD) {};
    BOOL(__stdcall* set_ivs_callback)(LONG, PlayIvsCallback, void*) {};
    BOOL(__stdcall* render_private_data)(LONG, BOOL, LONG) {};  // optional
    BOOL(__stdcall* play)(LONG, HWND) {};
    BOOL(__stdcall* input_data)(LONG, PBYTE, DWORD) {};
    BOOL(__stdcall* stop)(LONG) {};
    BOOL(__stdcall* close_stream)(LONG) {};
    DWORD(__stdcall* get_last_error)(LONG) {};
    DWORD(__stdcall* get_sdk_version)() {};

    template <typename T>
    void bind(T& target, const char* name, bool required) {
        target = reinterpret_cast<T>(GetProcAddress(module, name));
        if (target == nullptr && required) {
            throw std::runtime_error(std::string("play.dll does not export ") + name);
        }
    }

    void load(const fs::path& directory) {
        const fs::path path = directory / "play.dll";
        module = LoadLibraryExW(path.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (module == nullptr) {
            throw std::runtime_error("Cannot load " + path.string() +
                                     " (Windows error " + std::to_string(GetLastError()) + ")");
        }
        bind(get_free_port, "PLAY_GetFreePort", true);
        bind(release_port, "PLAY_ReleasePort", true);
        bind(set_stream_open_mode, "PLAY_SetStreamOpenMode", true);
        bind(open_stream, "PLAY_OpenStream", true);
        bind(set_ivs_callback, "PLAY_SetIVSCallBack", true);
        bind(render_private_data, "PLAY_RenderPrivateData", false);
        bind(play, "PLAY_Play", true);
        bind(input_data, "PLAY_InputData", true);
        bind(stop, "PLAY_Stop", true);
        bind(close_stream, "PLAY_CloseStream", true);
        bind(get_last_error, "PLAY_GetLastError", true);
        bind(get_sdk_version, "PLAY_GetSdkVersion", true);
    }

    std::string error(LONG port) const {
        std::ostringstream out;
        out << "PlaySDK error " << get_last_error(port);
        return out.str();
    }

    void unload() {
        if (module != nullptr) {
            FreeLibrary(module);
            module = nullptr;
        }
    }
};

fs::path executable_directory() {
    std::wstring buffer(MAX_PATH, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(),
                                            static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        throw std::runtime_error("Cannot resolve the executable directory");
    }
    buffer.resize(length);
    return fs::path(buffer).parent_path();
}

// ---------------------------------------------------------------------------
// Bounded asynchronous writer. SDK callbacks only copy and enqueue.

struct IvsRecord {
    std::chrono::system_clock::time_point received_at;
    double elapsed_ms{};
    LONG type{};
    LONG length{};
    LONG frame_sequence{};
    bool truncated{};
    std::vector<unsigned char> payload;
};

struct TypeStats {
    std::uint64_t count{};
    std::uint64_t total_bytes{};
    LONG min_length{};
    LONG max_length{};
    double first_elapsed_ms{};
    double last_elapsed_ms{};
};

class IvsWriter {
public:
    // File mode stores every frame for offline analysis. Stream mode writes
    // one "ivs_frame=" line per record to stdout for the Python collector;
    // it is not stored here, so the file-size cap does not apply.
    explicit IvsWriter(const fs::path& path)
        : output_(path, std::ios::binary), worker_(&IvsWriter::run, this) {
        if (!output_) {
            throw std::runtime_error("Cannot create " + path.string());
        }
    }

    struct StdoutTag {};
    explicit IvsWriter(StdoutTag) : to_stdout_(true), worker_(&IvsWriter::run, this) {}

    ~IvsWriter() { stop(); }

    IvsWriter(const IvsWriter&) = delete;
    IvsWriter& operator=(const IvsWriter&) = delete;

    void enqueue(IvsRecord record) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (queue_.size() >= kMaxQueuedRecords) {
                ++dropped_;
                return;
            }
            queue_.push(std::move(record));
        }
        condition_.notify_one();
    }

    void stop() {
        bool expected = false;
        if (!stopping_.compare_exchange_strong(expected, true)) {
            return;
        }
        condition_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
        output_.flush();
    }

    std::uint64_t dropped() const { return dropped_; }
    std::uint64_t write_errors() const { return write_errors_; }
    std::uint64_t payload_limit_hits() const { return payload_limit_hits_; }

private:
    void run() {
        while (true) {
            IvsRecord record;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                condition_.wait(lock, [this] { return stopping_ || !queue_.empty(); });
                if (queue_.empty()) {
                    if (stopping_) return;
                    continue;
                }
                record = std::move(queue_.front());
                queue_.pop();
            }
            write(record);
        }
    }

    void write(const IvsRecord& record) {
        std::ostringstream line;
        line << "{\"kind\":\"ivs\",\"received_at\":\"" << format_utc_time(record.received_at)
             << "\",\"elapsed_ms\":" << std::fixed << std::setprecision(1) << record.elapsed_ms
             << ",\"type\":" << record.type << ",\"type_name\":\"" << ivs_type_name(record.type)
             << "\",\"length\":" << record.length
             << ",\"frame_sequence\":" << record.frame_sequence
             << ",\"truncated\":" << (record.truncated ? "true" : "false");

        if (to_stdout_) {
            line << ",\"encoding\":\"hex\",\"payload\":\"" << to_hex(record.payload) << "\"}";
            std::lock_guard<std::mutex> lock(g_console_mutex);
            std::cout << "ivs_frame=" << line.str() << '\n' << std::flush;
            if (!std::cout) {
                ++write_errors_;
                std::cout.clear();
            }
            return;
        }

        if (written_payload_bytes_ + record.payload.size() > kMaxWrittenPayloadBytes) {
            ++payload_limit_hits_;
            line << ",\"encoding\":\"omitted\",\"payload\":null}";
        } else {
            written_payload_bytes_ += record.payload.size();
            const bool as_text = record.type == 5 && is_valid_utf8_text(record.payload);
            if (as_text) {
                const auto end = std::find(record.payload.begin(), record.payload.end(), '\0');
                line << ",\"encoding\":\"text\",\"payload\":\""
                     << json_escape(std::string(record.payload.begin(), end)) << "\"}";
            } else {
                line << ",\"encoding\":\"hex\",\"payload\":\"" << to_hex(record.payload)
                     << "\"}";
            }
        }
        output_ << line.str() << '\n';
        if (!output_) {
            ++write_errors_;
            output_.clear();
        }
    }

    std::ofstream output_;
    bool to_stdout_{false};
    std::mutex mutex_;
    std::condition_variable condition_;
    std::queue<IvsRecord> queue_;
    std::atomic<bool> stopping_{false};
    std::atomic<std::uint64_t> dropped_{0};
    std::atomic<std::uint64_t> write_errors_{0};
    std::atomic<std::uint64_t> payload_limit_hits_{0};
    std::uint64_t written_payload_bytes_{0};
    std::thread worker_;
};

// ---------------------------------------------------------------------------
// Shared state for the NetSDK and PlaySDK callbacks.

struct ProbeContext {
    PlaySdk* play{};
    LONG port{-1};
    IvsWriter* writer{};
    bool stream_track_records_only{};  // collector mode forwards type 7 only
    std::chrono::steady_clock::time_point started_at{};
    std::atomic<std::uint64_t> video_bytes{0};
    std::atomic<std::uint64_t> video_packets{0};
    std::atomic<std::uint64_t> input_failures{0};
    std::atomic<std::uint64_t> ivs_frames{0};
    std::mutex stats_mutex;
    std::map<LONG, TypeStats> stats;

    double elapsed_ms() const {
        return std::chrono::duration<double, std::milli>(
                   std::chrono::steady_clock::now() - started_at)
            .count();
    }
};

void CALLBACK on_real_data(LLONG, DWORD data_type, BYTE* buffer, DWORD size, LLONG, LDWORD user) {
    if (user == 0 || buffer == nullptr || size == 0 || data_type != 0) {
        return;  // dwDataType 0 is the raw private stream (REALDATA_FLAG_RAW_DATA).
    }
    auto* context = reinterpret_cast<ProbeContext*>(user);
    context->video_bytes += size;
    ++context->video_packets;
    if (!context->play->input_data(context->port, buffer, size)) {
        ++context->input_failures;  // PlaySDK buffer full; counted, never silent.
    }
}

void __stdcall on_ivs(char* buffer, LONG type, LONG length, LONG frame_sequence, void*,
                      void* user) {
    if (user == nullptr) {
        return;
    }
    auto* context = static_cast<ProbeContext*>(user);
    IvsRecord record;
    record.received_at = std::chrono::system_clock::now();
    record.elapsed_ms = context->elapsed_ms();
    record.type = type;
    record.length = length;
    record.frame_sequence = frame_sequence;
    if (buffer != nullptr && length > 0) {
        const auto copied = std::min<std::size_t>(static_cast<std::size_t>(length),
                                                  kMaxPayloadBytes);
        record.truncated = copied < static_cast<std::size_t>(length);
        record.payload.assign(reinterpret_cast<unsigned char*>(buffer),
                              reinterpret_cast<unsigned char*>(buffer) + copied);
    }

    bool first_of_type = false;
    {
        std::lock_guard<std::mutex> lock(context->stats_mutex);
        auto& stats = context->stats[type];
        first_of_type = stats.count == 0;
        if (first_of_type) {
            stats.min_length = length;
            stats.max_length = length;
            stats.first_elapsed_ms = record.elapsed_ms;
        }
        ++stats.count;
        stats.total_bytes += static_cast<std::uint64_t>(std::max<LONG>(length, 0));
        stats.min_length = std::min(stats.min_length, length);
        stats.max_length = std::max(stats.max_length, length);
        stats.last_elapsed_ms = record.elapsed_ms;
    }
    ++context->ivs_frames;

    if (first_of_type) {
        std::lock_guard<std::mutex> lock(g_console_mutex);
        std::cout << "first_ivs type=" << type << " (" << ivs_type_name(type) << ") length="
                  << length << " frame_sequence=" << frame_sequence << " at="
                  << std::fixed << std::setprecision(1) << record.elapsed_ms / 1000.0
                  << "s\n" << std::flush;
    }
    if (context->stream_track_records_only && type != kTrackRecordType) {
        return;  // counted in the statistics above, but not forwarded
    }
    context->writer->enqueue(std::move(record));
}

void CALLBACK on_disconnect(LLONG, char*, LONG, LDWORD) {
    std::lock_guard<std::mutex> lock(g_console_mutex);
    std::cerr << "Camera disconnected; waiting for SDK auto-reconnect.\n";
}

void CALLBACK on_reconnect(LLONG, char*, LONG, LDWORD) {
    std::lock_guard<std::mutex> lock(g_console_mutex);
    std::cout << "Camera reconnected.\n";
}

struct Options {
    fs::path env_path{".env"};
    fs::path output_root{"experiments/dahua-netsdk/output"};
    std::string stream{"main"};
    int seconds{0};
    bool to_stdout{false};  // collector mode: stream type 7 records, no files
    int stall_seconds{20};  // collector mode: exit 2 if no video arrives
};

Options parse_options(int argc, char** argv) {
    Options options;
    std::vector<std::string> positional;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--stream" && i + 1 < argc) {
            options.stream = argv[++i];
        } else if (arg == "--seconds" && i + 1 < argc) {
            options.seconds = std::stoi(argv[++i]);
        } else if (arg == "--stdout") {
            options.to_stdout = true;
        } else if (arg == "--stall-seconds" && i + 1 < argc) {
            options.stall_seconds = std::stoi(argv[++i]);
        } else if (arg.rfind("--", 0) == 0) {
            throw std::runtime_error("Unknown option: " + arg);
        } else {
            positional.push_back(arg);
        }
    }
    if (positional.size() > 2) {
        throw std::runtime_error(
            "Usage: dahua-ivs-probe [ENV_FILE|-] [OUTPUT_DIR] [--stream main|sub] [--seconds N]"
            " [--stdout [--stall-seconds N]]");
    }
    if (options.stall_seconds < 0) {
        throw std::runtime_error("--stall-seconds must be zero (disabled) or positive");
    }
    if (!positional.empty()) options.env_path = positional[0];
    if (positional.size() > 1) options.output_root = positional[1];
    if (options.stream != "main" && options.stream != "sub") {
        throw std::runtime_error("--stream must be 'main' or 'sub'");
    }
    if (options.seconds < 0) {
        throw std::runtime_error("--seconds must be zero (wait for Enter) or positive");
    }
    return options;
}

void write_summary(const fs::path& path, const std::string& header_json,
                   ProbeContext& context, const IvsWriter& writer,
                   const std::chrono::system_clock::time_point& ended_at) {
    std::ofstream out(path);
    if (!out) {
        throw std::runtime_error("Cannot write summary: " + path.string());
    }
    out << "{\n" << header_json
        << "  \"ended_at\": \"" << format_utc_time(ended_at) << "\",\n"
        << "  \"duration_seconds\": " << std::fixed << std::setprecision(1)
        << context.elapsed_ms() / 1000.0 << ",\n"
        << "  \"video_bytes\": " << context.video_bytes << ",\n"
        << "  \"video_packets\": " << context.video_packets << ",\n"
        << "  \"play_input_failures\": " << context.input_failures << ",\n"
        << "  \"ivs_frames\": " << context.ivs_frames << ",\n"
        << "  \"dropped_records\": " << writer.dropped() << ",\n"
        << "  \"write_errors\": " << writer.write_errors() << ",\n"
        << "  \"payloads_omitted_by_size_limit\": " << writer.payload_limit_hits() << ",\n"
        << "  \"ivs_types\": [\n";
    std::lock_guard<std::mutex> lock(context.stats_mutex);
    std::size_t index = 0;
    for (const auto& [type, stats] : context.stats) {
        out << "    {\"type\": " << type << ", \"type_name\": \"" << ivs_type_name(type)
            << "\", \"count\": " << stats.count << ", \"total_bytes\": " << stats.total_bytes
            << ", \"min_length\": " << stats.min_length
            << ", \"max_length\": " << stats.max_length
            << ", \"first_seconds\": " << stats.first_elapsed_ms / 1000.0
            << ", \"last_seconds\": " << stats.last_elapsed_ms / 1000.0 << '}'
            << (++index == context.stats.size() ? "\n" : ",\n");
    }
    out << "  ]\n}\n";
}

}  // namespace

int main(int argc, char** argv) {
    static_assert(sizeof(LDWORD) >= sizeof(void*), "LDWORD cannot hold a pointer");

    PlaySdk play;
    ProbeContext context;
    bool initialized = false;
    bool stream_open = false;
    bool playing = false;
    bool port_acquired = false;
    LLONG login_handle = 0;
    LLONG real_handle = 0;
    std::unique_ptr<IvsWriter> writer;
    std::atomic<bool> status_running{false};
    std::thread status_thread;

    auto cleanup = [&]() {
        if (real_handle != 0 && !CLIENT_StopRealPlayEx(real_handle)) {
            std::cerr << "CLIENT_StopRealPlayEx failed: " << sdk_error() << '\n';
        }
        real_handle = 0;
        status_running = false;
        if (status_thread.joinable()) status_thread.join();
        if (playing && !play.stop(context.port)) {
            std::cerr << "PLAY_Stop failed: " << play.error(context.port) << '\n';
        }
        playing = false;
        if (stream_open) {
            play.set_ivs_callback(context.port, nullptr, nullptr);
            if (!play.close_stream(context.port)) {
                std::cerr << "PLAY_CloseStream failed: " << play.error(context.port) << '\n';
            }
        }
        stream_open = false;
        if (port_acquired && !play.release_port(context.port)) {
            std::cerr << "PLAY_ReleasePort failed\n";
        }
        port_acquired = false;
        if (writer) writer->stop();
        if (login_handle != 0 && !CLIENT_Logout(login_handle)) {
            std::cerr << "CLIENT_Logout failed: " << sdk_error() << '\n';
        }
        login_handle = 0;
        if (initialized) CLIENT_Cleanup();
        initialized = false;
    };

    try {
        const Options options = parse_options(argc, argv);
        const auto env = options.env_path == fs::path("-") ? load_process_env()
                                                           : load_env(options.env_path);
        const auto& host = require_env(env, "DAHUA_HOST");
        const auto& user = require_env(env, "DAHUA_USER");
        const auto& password = require_env(env, "DAHUA_PASSWORD");
        const int port = std::stoi(require_env(env, "DAHUA_PORT"));
        if (port < 1 || port > 65535) {
            throw std::runtime_error("DAHUA_PORT is outside the valid TCP port range");
        }

        play.load(executable_directory());

        if (!CLIENT_Init(on_disconnect, 0)) {
            throw std::runtime_error("CLIENT_Init failed: " + sdk_error());
        }
        initialized = true;
        CLIENT_SetAutoReconnect(on_reconnect, 0);

        NET_PARAM network{};
        network.nConnectTime = 5000;
        network.nConnectTryNum = 3;
        CLIENT_SetNetworkParam(&network);

        NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY login_in{};
        login_in.dwSize = sizeof(login_in);
        strncpy_s(login_in.szIP, host.c_str(), _TRUNCATE);
        strncpy_s(login_in.szUserName, user.c_str(), _TRUNCATE);
        strncpy_s(login_in.szPassword, password.c_str(), _TRUNCATE);
        login_in.nPort = port;
        login_in.emSpecCap = EM_LOGIN_SPEC_CAP_TCP;
        NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY login_out{};
        login_out.dwSize = sizeof(login_out);
        login_handle = CLIENT_LoginWithHighLevelSecurity(&login_in, &login_out);
        if (login_handle == 0) {
            throw std::runtime_error("Camera login failed: " + sdk_error());
        }

        // Identify the camera from its own report so the evidence never
        // depends on hand-written inventory notes. The serial is not printed.
        DHDEV_VERSION_INFO version{};
        int returned = 0;
        std::string model = "unavailable";
        std::string detail_model = "unavailable";
        std::string firmware = "unavailable";
        if (CLIENT_QueryDevState(login_handle, DH_DEVSTATE_SOFTWARE,
                                 reinterpret_cast<char*>(&version), sizeof(version),
                                 &returned, 3000)) {
            model = fixed_string(version.szDevType);
            detail_model = fixed_string(version.szDetailType);
            firmware = fixed_string(version.szSoftWareVersion);
        } else {
            std::cerr << "Device version query failed: " << sdk_error() << '\n';
        }

        const auto started_wall = std::chrono::system_clock::now();
        fs::path session_dir;
        if (options.to_stdout) {
            writer = std::make_unique<IvsWriter>(IvsWriter::StdoutTag{});
        } else {
            session_dir = options.output_root / "ivs-probe" /
                          (compact_utc_stamp(started_wall) + "_" + options.stream);
            fs::create_directories(session_dir);
            writer = std::make_unique<IvsWriter>(session_dir / "ivs-frames.jsonl");
        }

        std::cout << "NetSDK version=" << CLIENT_GetSDKVersion()
                  << " PlaySDK version=" << play.get_sdk_version() << '\n'
                  << "Login succeeded; channels="
                  << static_cast<int>(login_out.stuDeviceInfo.nChanNum) << '\n'
                  << "camera_model=" << model << '\n'
                  << "camera_detail_model=" << detail_model << '\n'
                  << "camera_firmware=" << firmware << '\n';
        if (!options.to_stdout) {
            std::cout << "session_dir=" << fs::absolute(session_dir).string() << '\n';
        }
        std::cout << std::flush;

        context.play = &play;
        context.writer = writer.get();
        context.stream_track_records_only = options.to_stdout;
        context.started_at = std::chrono::steady_clock::now();

        if (!play.get_free_port(&context.port)) {
            throw std::runtime_error("PLAY_GetFreePort failed");
        }
        port_acquired = true;
        if (!play.set_stream_open_mode(context.port, kStreamRealtime)) {
            throw std::runtime_error("PLAY_SetStreamOpenMode failed: " + play.error(context.port));
        }
        if (!play.open_stream(context.port, nullptr, 0, kPlayBufferBytes)) {
            throw std::runtime_error("PLAY_OpenStream failed: " + play.error(context.port));
        }
        stream_open = true;
        if (!play.set_ivs_callback(context.port, on_ivs, &context)) {
            throw std::runtime_error("PLAY_SetIVSCallBack failed: " + play.error(context.port));
        }
        // Rendering flag for rule/target private data. There is no window, so
        // nothing is drawn; it is enabled in case the parser depends on it.
        bool render_private_data = false;
        if (play.render_private_data != nullptr) {
            render_private_data = play.render_private_data(context.port, TRUE, 0) != FALSE;
        }
        if (!play.play(context.port, nullptr)) {
            throw std::runtime_error("PLAY_Play failed: " + play.error(context.port));
        }
        playing = true;

        const DH_RealPlayType stream_type =
            options.stream == "main" ? DH_RType_Realplay_0 : DH_RType_Realplay_1;
        real_handle = CLIENT_RealPlayEx(login_handle, 0, nullptr, stream_type);
        if (real_handle == 0) {
            throw std::runtime_error("CLIENT_RealPlayEx failed: " + sdk_error());
        }
        if (!CLIENT_SetRealDataCallBackEx2(real_handle, on_real_data,
                                           reinterpret_cast<LDWORD>(&context),
                                           REALDATA_FLAG_RAW_DATA)) {
            throw std::runtime_error("CLIENT_SetRealDataCallBackEx2 failed: " + sdk_error());
        }

        std::ostringstream header;
        header << "  \"schema_version\": \"dahua_ivs_probe.v1\",\n"
               << "  \"started_at\": \"" << format_utc_time(started_wall) << "\",\n"
               << "  \"stream\": \"" << options.stream << "\",\n"
               << "  \"camera_model\": \"" << json_escape(model) << "\",\n"
               << "  \"camera_detail_model\": \"" << json_escape(detail_model) << "\",\n"
               << "  \"camera_firmware\": \"" << json_escape(firmware) << "\",\n"
               << "  \"netsdk_version\": " << CLIENT_GetSDKVersion() << ",\n"
               << "  \"playsdk_version\": " << play.get_sdk_version() << ",\n"
               << "  \"render_private_data\": " << (render_private_data ? "true" : "false")
               << ",\n";

        std::cout << "Streaming (" << options.stream << "). "
                  << (options.seconds > 0 ? "Stopping after " + std::to_string(options.seconds) +
                                                " seconds."
                                          : std::string("Press Enter to stop."))
                  << '\n' << std::flush;

        status_running = true;
        const bool collector_mode = options.to_stdout;
        const int stall_seconds = options.stall_seconds;
        status_thread = std::thread([&, collector_mode, stall_seconds]() {
            // Collector mode reports rarely and watches for a frozen stream:
            // if no video arrives for stall_seconds it exits with code 2 so
            // the collector restarts it instead of silently losing the lane.
            const auto status_every = std::chrono::seconds(collector_mode ? 30 : 2);
            auto next_status = std::chrono::steady_clock::now() + status_every;
            std::uint64_t last_video_bytes = context.video_bytes;
            auto last_progress = std::chrono::steady_clock::now();
            while (status_running) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                const auto now = std::chrono::steady_clock::now();
                const std::uint64_t video_bytes = context.video_bytes;
                if (video_bytes != last_video_bytes) {
                    last_video_bytes = video_bytes;
                    last_progress = now;
                }
                if (collector_mode && stall_seconds > 0 &&
                    now - last_progress >= std::chrono::seconds(stall_seconds)) {
                    {
                        std::lock_guard<std::mutex> lock(g_console_mutex);
                        std::cout << "stream_stalled seconds=" << stall_seconds << '\n'
                                  << std::flush;
                    }
                    request_stop(2);
                    break;
                }
                if (now < next_status) continue;
                next_status = now + status_every;
                std::ostringstream line;
                line << "status t=" << std::fixed << std::setprecision(0)
                     << context.elapsed_ms() / 1000.0 << "s video_kib="
                     << context.video_bytes / 1024 << " ivs_frames=" << context.ivs_frames
                     << " input_failures=" << context.input_failures
                     << " dropped=" << writer->dropped() << " types={";
                {
                    std::lock_guard<std::mutex> lock(context.stats_mutex);
                    bool first = true;
                    for (const auto& [type, stats] : context.stats) {
                        line << (first ? "" : ",") << ivs_type_name(type) << ':' << stats.count;
                        first = false;
                    }
                }
                line << '}';
                std::lock_guard<std::mutex> lock(g_console_mutex);
                std::cout << line.str() << '\n' << std::flush;
            }
        });

        if (options.seconds > 0) {
            std::unique_lock<std::mutex> lock(g_stop_mutex);
            g_stop_condition.wait_for(lock, std::chrono::seconds(options.seconds),
                                      [] { return g_stop_requested; });
        } else {
            // Enter or a closed stdin (the collector exiting) both stop cleanly.
            std::thread([] {
                std::string ignored;
                std::getline(std::cin, ignored);
                request_stop(0);
            }).detach();
            std::unique_lock<std::mutex> lock(g_stop_mutex);
            g_stop_condition.wait(lock, [] { return g_stop_requested; });
        }
        int exit_code = 0;
        {
            std::lock_guard<std::mutex> lock(g_stop_mutex);
            exit_code = g_exit_code;
        }

        // Stop input first so no callback races the summary.
        if (!CLIENT_StopRealPlayEx(real_handle)) {
            std::cerr << "CLIENT_StopRealPlayEx failed: " << sdk_error() << '\n';
        }
        real_handle = 0;
        const auto ended_wall = std::chrono::system_clock::now();
        cleanup();
        if (!options.to_stdout) {
            write_summary(session_dir / "summary.json", header.str(), context, *writer,
                          ended_wall);
        }
        play.unload();

        std::cout << "ivs_frames=" << context.ivs_frames
                  << " video_kib=" << context.video_bytes / 1024
                  << " dropped=" << writer->dropped()
                  << " write_errors=" << writer->write_errors();
        if (!options.to_stdout) {
            std::cout << " summary=" << fs::absolute(session_dir / "summary.json").string();
        }
        std::cout << '\n'
                  << (exit_code == 0 ? "Clean shutdown completed.\n"
                                     : "Stopped after stream stall.\n")
                  << std::flush;
        return exit_code;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        cleanup();
        play.unload();
        return 1;
    }
}
