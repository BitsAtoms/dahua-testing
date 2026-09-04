#include <windows.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "dhnetsdk.h"

namespace fs = std::filesystem;

namespace {

constexpr std::size_t kMaxDynamicImages = 64;
constexpr int kPictureBufferBytes = 16 * 1024 * 1024;

struct ImageDescriptor {
    std::string type;
    std::string source;
    std::uint32_t offset{};
    std::uint32_t length{};
    std::uint32_t width{};
    std::uint32_t height{};
};

struct EventRecord {
    DWORD alarm_type{};
    int callback_sequence{};
    int transfer_state{-1};
    int channel{};
    int action{};
    int event_id{};
    int group_id{};
    int count_in_group{};
    int index_in_group{};
    unsigned int local_track_id{};
    std::int64_t unique_id{};
    std::uint64_t numeric_event_uuid{};
    unsigned int start_sequence{};
    unsigned int end_sequence{};
    int class_type{};
    int detect_object{};
    int image_info_count{};
    double pts{};
    std::string event_name;
    std::string event_uuid;
    std::string legacy_event_uuid;
    std::string object_uuid;
    std::string serial_uuid;
    std::string source_id;
    std::string additional_code;
    NET_TIME_EX utc{};
    NET_TIME_EX2 real_utc{};
    bool has_real_utc{};
    std::vector<ImageDescriptor> images;
    std::vector<BYTE> buffer;
};

std::mutex g_console_mutex;

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

std::string format_time(const NET_TIME_EX& time) {
    std::ostringstream out;
    out << std::setfill('0')
        << std::setw(4) << time.dwYear
        << std::setw(2) << time.dwMonth
        << std::setw(2) << time.dwDay << 'T'
        << std::setw(2) << time.dwHour
        << std::setw(2) << time.dwMinute
        << std::setw(2) << time.dwSecond << '-'
        << std::setw(3) << time.dwMillisecond;
    return out.str();
}

std::string action_name(int action) {
    if (action == 1) return "Start";
    if (action == 2) return "Stop";
    return "Unknown";
}

std::string optional_id(unsigned int value) {
    return value == 0 ? "null" : std::to_string(value);
}

std::string image_type_name(EM_IMAGE_TYPE_EX2 type) {
    switch (type) {
        case EM_IMAGE_TYPE_SCENE_IMAGE: return "panoramic";
        case EM_IMAGE_TYPE_GLOBAL_SCENE: return "global-scene";
        case EM_IMAGE_TYPE_THUM_IMAGE: return "thumbnail";
        case EM_IMAGE_TYPE_FACE_SCENE_IMAGE: return "face-panoramic";
        case EM_IMAGE_TYPE_FACE_IMAGE: return "face";
        case EM_IMAGE_TYPE_HUMAN_IMAGE: return "body";
        case EM_IMAGE_TYPE_ALONG_WITH_FACE_HUMAN_IMAGE: return "body-with-face";
        case EM_IMAGE_TYPE_ALONG_WITH_FACE_HUMAN_SCENE_IMAGE: return "body-with-face-panoramic";
        default: return "unknown";
    }
}

void add_image(
    EventRecord& record,
    std::string type,
    std::string source,
    std::uint32_t offset,
    std::uint32_t length,
    std::uint32_t width,
    std::uint32_t height) {
    if (length == 0) {
        return;
    }
    record.images.push_back(
        {std::move(type), std::move(source), offset, length, width, height});
}

bool valid_range(const ImageDescriptor& image, std::size_t buffer_size) {
    return image.offset <= buffer_size && image.length <= buffer_size - image.offset;
}

bool looks_like_jpeg(const BYTE* data, std::size_t size) {
    return size >= 4 && data[0] == 0xFF && data[1] == 0xD8 &&
           data[size - 2] == 0xFF && data[size - 1] == 0xD9;
}

class EventWriter {
public:
    explicit EventWriter(fs::path output_dir)
        : output_dir_(std::move(output_dir)), worker_(&EventWriter::run, this) {
        fs::create_directories(output_dir_);
    }

    ~EventWriter() { stop(); }

    EventWriter(const EventWriter&) = delete;
    EventWriter& operator=(const EventWriter&) = delete;

    void enqueue(EventRecord record) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
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
    }

private:
    void run() {
        while (true) {
            EventRecord record;
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
            try {
                write(record);
            } catch (const std::exception& error) {
                std::lock_guard<std::mutex> lock(g_console_mutex);
                std::cerr << "Failed to persist event: " << error.what() << '\n';
            }
        }
    }

    void write(const EventRecord& record) {
        const std::string timestamp = format_time(record.utc);
        std::ostringstream prefix_builder;
        prefix_builder << timestamp << "_group-" << record.group_id
                       << "_event-" << record.event_id
                       << "_seq-" << record.callback_sequence;
        const std::string prefix = prefix_builder.str();

        std::set<std::tuple<std::uint32_t, std::uint32_t, std::string>> seen;
        std::vector<std::pair<ImageDescriptor, std::string>> saved;
        int image_index = 0;

        for (const auto& image : record.images) {
            if (!valid_range(image, record.buffer.size())) {
                continue;
            }
            const auto key = std::make_tuple(image.offset, image.length, image.type);
            if (!seen.insert(key).second) {
                continue;
            }
            const BYTE* data = record.buffer.data() + image.offset;
            if (!looks_like_jpeg(data, image.length)) {
                continue;
            }

            std::ostringstream filename;
            filename << prefix << '_' << image.type << '-' << image_index++ << ".jpg";
            const fs::path path = output_dir_ / filename.str();
            std::ofstream output(path, std::ios::binary);
            output.write(reinterpret_cast<const char*>(data), image.length);
            if (!output) {
                throw std::runtime_error("Cannot write image: " + path.string());
            }
            saved.emplace_back(image, filename.str());
        }

        const fs::path json_path = output_dir_ / (prefix + "_event.json");
        std::ofstream json(json_path);
        if (!json) {
            throw std::runtime_error("Cannot write event JSON: " + json_path.string());
        }

        json << "{\n"
             << "  \"source\": \"dahua\",\n"
             << "  \"code\": \"HumanTrait\",\n"
             << "  \"alarm_type\": " << record.alarm_type << ",\n"
             << "  \"action\": \"" << action_name(record.action) << "\",\n"
             << "  \"raw_action\": " << record.action << ",\n"
             << "  \"channel\": " << record.channel << ",\n"
             << "  \"local_track_id\": " << optional_id(record.local_track_id) << ",\n"
             << "  \"local_track_id_source\": \"netsdk.nObjectID\",\n"
             << "  \"event_id\": " << record.event_id << ",\n"
             << "  \"group_id\": " << record.group_id << ",\n"
             << "  \"count_in_group\": " << record.count_in_group << ",\n"
             << "  \"index_in_group\": " << record.index_in_group << ",\n"
             << "  \"event_uuid\": \"" << json_escape(record.event_uuid) << "\",\n"
             << "  \"legacy_event_uuid\": \"" << json_escape(record.legacy_event_uuid) << "\",\n"
             << "  \"numeric_event_uuid\": " << record.numeric_event_uuid << ",\n"
             << "  \"unique_id\": " << record.unique_id << ",\n"
             << "  \"object_uuid\": \"" << json_escape(record.object_uuid) << "\",\n"
             << "  \"serial_uuid\": \"" << json_escape(record.serial_uuid) << "\",\n"
             << "  \"source_id\": \"" << json_escape(record.source_id) << "\",\n"
             << "  \"additional_code\": \"" << json_escape(record.additional_code) << "\",\n"
             << "  \"start_sequence\": " << record.start_sequence << ",\n"
             << "  \"end_sequence\": " << record.end_sequence << ",\n"
             << "  \"class_type\": " << record.class_type << ",\n"
             << "  \"detect_object\": " << record.detect_object << ",\n"
             << "  \"reported_image_count\": " << record.image_info_count << ",\n"
             << "  \"pts\": " << std::setprecision(17) << record.pts << ",\n"
             << "  \"event_name\": \"" << json_escape(record.event_name) << "\",\n"
             << "  \"timestamp\": \"" << timestamp << "\",\n"
             << "  \"callback_sequence\": " << record.callback_sequence << ",\n"
             << "  \"transfer_state\": " << record.transfer_state << ",\n"
             << "  \"buffer_size\": " << record.buffer.size() << ",\n"
             << "  \"image_descriptors\": [\n";

        for (std::size_t i = 0; i < record.images.size(); ++i) {
            const auto& image = record.images[i];
            json << "    {\"type\": \"" << json_escape(image.type)
                 << "\", \"source_field\": \"" << json_escape(image.source)
                 << "\", \"offset\": " << image.offset
                 << ", \"length\": " << image.length
                 << ", \"width\": " << image.width
                 << ", \"height\": " << image.height
                 << ", \"range_valid\": "
                 << (valid_range(image, record.buffer.size()) ? "true" : "false") << '}';
            json << (i + 1 == record.images.size() ? "\n" : ",\n");
        }

        json << "  ],\n  \"snapshots\": [\n";
        for (std::size_t i = 0; i < saved.size(); ++i) {
            json << "    {\"type\": \"" << json_escape(saved[i].first.type)
                 << "\", \"file\": \"" << json_escape(saved[i].second) << "\"}";
            json << (i + 1 == saved.size() ? "\n" : ",\n");
        }
        json << "  ]\n}\n";
        json.close();
        if (!json) {
            throw std::runtime_error("Cannot finalize event JSON: " + json_path.string());
        }

        std::lock_guard<std::mutex> lock(g_console_mutex);
        std::cout << "camera=dahua\n"
                  << "code=HumanTrait\n"
                  << "action=" << action_name(record.action) << '\n'
                  << "local_track_id="
                  << (record.local_track_id == 0
                          ? "unavailable (requires CGI correlation)"
                          : std::to_string(record.local_track_id))
                  << '\n'
                  << "correlation_group_id=" << record.group_id << '\n'
                  << "event_id=" << record.event_id << '\n'
                  << "event_uuid=" << record.event_uuid << '\n'
                  << "buffer_size=" << record.buffer.size() << '\n'
                  << "saved_jpegs=" << saved.size() << '\n'
                  << "event_json=" << fs::absolute(json_path).string() << "\n\n"
                  << std::flush;
    }

    fs::path output_dir_;
    std::mutex mutex_;
    std::condition_variable condition_;
    std::queue<EventRecord> queue_;
    std::atomic<bool> stopping_{false};
    std::thread worker_;
};

void copy_human_trait_event(
    EventWriter& writer,
    DWORD alarm_type,
    const DEV_EVENT_HUMANTRAIT_INFO& info,
    const BYTE* buffer,
    DWORD buffer_size,
    int callback_sequence,
    void* reserved) {
    EventRecord record;
    record.alarm_type = alarm_type;
    record.callback_sequence = callback_sequence;
    if (reserved != nullptr) {
        record.transfer_state = *static_cast<int*>(reserved);
    }
    record.channel = info.nChannelID;
    record.action = info.nAction;
    record.event_id = info.nEventID;
    record.group_id = info.nGroupID;
    record.count_in_group = info.nCountInGroup;
    record.index_in_group = info.nIndexInGroup;
    record.local_track_id = info.nObjectID;
    record.unique_id = info.nUniqueID;
    record.numeric_event_uuid = info.nEventUUID;
    record.start_sequence = info.nStartSequence;
    record.end_sequence = info.nEndSequence;
    record.class_type = static_cast<int>(info.emClassType);
    record.detect_object = static_cast<int>(info.emDetectObject);
    record.image_info_count = info.nImageInfoNum;
    record.pts = info.PTS;
    record.event_name = fixed_string(info.szName);
    record.event_uuid = fixed_string(info.szEventUUIDStr);
    record.legacy_event_uuid = fixed_string(info.szEventUUID);
    record.object_uuid = fixed_string(info.szObjectUUID);
    record.serial_uuid = fixed_string(info.szSerialUUID);
    record.source_id = fixed_string(info.szSourceID);
    record.additional_code = fixed_string(info.stuHumanTrait.szAdditionalCode);
    record.utc = info.UTC;
    record.real_utc = info.stuEventInfoEx.stuRealUTCEx;
    record.has_real_utc = info.stuEventInfoEx.bRealUTC != FALSE;

    add_image(record, "body", "stuHumanImage", info.stuHumanImage.nOffSet,
              info.stuHumanImage.nLength, info.stuHumanImage.nWidth,
              info.stuHumanImage.nHeight);
    add_image(record, "face", "stuFaceImage", info.stuFaceImage.nOffSet,
              info.stuFaceImage.nLength, info.stuFaceImage.nWidth,
              info.stuFaceImage.nHeight);
    add_image(record, "panoramic", "stuSceneImage", info.stuSceneImage.nOffSet,
              info.stuSceneImage.nLength, info.stuSceneImage.nWidth,
              info.stuSceneImage.nHeight);
    add_image(record, "face-panoramic", "stuFaceSceneImage",
              info.stuFaceSceneImage.nOffSet, info.stuFaceSceneImage.nLength,
              info.stuFaceSceneImage.nWidth, info.stuFaceSceneImage.nHeight);

    const int fixed_count = std::clamp(info.nImageInfoNum, 0, 32);
    for (int i = 0; i < fixed_count; ++i) {
        const auto& image = info.stuImageInfo[i];
        add_image(record, image_type_name(image.emType), "stuImageInfo",
                  image.nOffset, image.nLength, 0, 0);
    }

    if (info.pstuImageInfo != nullptr && info.nImageInfoNum > 0) {
        const auto dynamic_count = std::min<std::size_t>(
            static_cast<std::size_t>(info.nImageInfoNum), kMaxDynamicImages);
        for (std::size_t i = 0; i < dynamic_count; ++i) {
            const auto& image = info.pstuImageInfo[i];
            add_image(record, image_type_name(image.emType), "pstuImageInfo",
                      image.nOffset, image.nLength, image.nWidth, image.nHeight);
        }
    }

    if (buffer != nullptr && buffer_size > 0) {
        record.buffer.assign(buffer, buffer + buffer_size);
    }
    writer.enqueue(std::move(record));
}

void CALLBACK on_disconnect(LLONG, char*, LONG, LDWORD) {
    std::lock_guard<std::mutex> lock(g_console_mutex);
    std::cerr << "Camera disconnected; waiting for SDK auto-reconnect.\n";
}

void CALLBACK on_reconnect(LLONG, char*, LONG, LDWORD) {
    std::lock_guard<std::mutex> lock(g_console_mutex);
    std::cout << "Camera reconnected.\n";
}

int CALLBACK on_analyzer_data(
    LLONG,
    DWORD alarm_type,
    void* alarm_info,
    BYTE* buffer,
    DWORD buffer_size,
    LDWORD user,
    int sequence,
    void* reserved) {
    if (user == 0 || alarm_info == nullptr) {
        return 0;
    }

    auto* writer = reinterpret_cast<EventWriter*>(user);
    if (alarm_type == EVENT_IVS_HUMANTRAIT) {
        copy_human_trait_event(
            *writer,
            alarm_type,
            *static_cast<DEV_EVENT_HUMANTRAIT_INFO*>(alarm_info),
            buffer,
            buffer_size,
            sequence,
            reserved);
    }
    return 0;
}

std::string sdk_error() {
    std::ostringstream out;
    out << "0x" << std::hex << std::uppercase << CLIENT_GetLastError();
    return out.str();
}

}  // namespace

int main(int argc, char** argv) {
    static_assert(sizeof(LDWORD) >= sizeof(void*), "LDWORD cannot hold a pointer");

    const fs::path env_path = argc > 1 ? fs::path(argv[1]) : fs::path(".env");
    const fs::path output_dir = argc > 2
        ? fs::path(argv[2])
        : fs::path("experiments/dahua-netsdk/output");

    bool initialized = false;
    LLONG login_handle = 0;
    LLONG subscription_handle = 0;

    try {
        const auto env = env_path == fs::path("-")
            ? load_process_env()
            : load_env(env_path);
        const auto& host = require_env(env, "DAHUA_HOST");
        const auto& user = require_env(env, "DAHUA_USER");
        const auto& password = require_env(env, "DAHUA_PASSWORD");
        const int port = std::stoi(require_env(env, "DAHUA_PORT"));
        if (port < 1 || port > 65535) {
            throw std::runtime_error("DAHUA_PORT is outside the valid TCP port range");
        }

        if (!CLIENT_Init(on_disconnect, 0)) {
            throw std::runtime_error("CLIENT_Init failed: " + sdk_error());
        }
        initialized = true;
        CLIENT_SetAutoReconnect(on_reconnect, 0);

        NET_PARAM network{};
        network.nConnectTime = 5000;
        network.nConnectTryNum = 3;
        network.nPicBufSize = kPictureBufferBytes;
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

        std::cout << "SDK version=" << CLIENT_GetSDKVersion() << '\n'
                  << "Login succeeded; channels="
                  << static_cast<int>(login_out.stuDeviceInfo.nChanNum) << '\n';

        EventWriter writer(output_dir);
        subscription_handle = CLIENT_RealLoadPictureEx(
            login_handle,
            0,
            EVENT_IVS_ALL,
            TRUE,
            on_analyzer_data,
            reinterpret_cast<LDWORD>(&writer),
            nullptr);
        if (subscription_handle == 0) {
            throw std::runtime_error("Event subscription failed: " + sdk_error());
        }

        std::cout << "Subscribed to intelligent events with pictures.\n"
                  << "Walk through the camera view, then press Enter to stop.\n";
        std::string ignored;
        std::getline(std::cin, ignored);

        if (!CLIENT_StopLoadPic(subscription_handle)) {
            std::cerr << "CLIENT_StopLoadPic failed: " << sdk_error() << '\n';
        }
        subscription_handle = 0;
        writer.stop();

        if (!CLIENT_Logout(login_handle)) {
            std::cerr << "CLIENT_Logout failed: " << sdk_error() << '\n';
        }
        login_handle = 0;
        CLIENT_Cleanup();
        initialized = false;
        std::cout << "Clean shutdown completed.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        if (subscription_handle != 0) {
            CLIENT_StopLoadPic(subscription_handle);
        }
        if (login_handle != 0) {
            CLIENT_Logout(login_handle);
        }
        if (initialized) {
            CLIENT_Cleanup();
        }
        return 1;
    }
}
