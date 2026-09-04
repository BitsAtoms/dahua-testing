cmake_minimum_required(VERSION 3.20)

project(dahua_netsdk_events LANGUAGES CXX)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "This experiment requires a 64-bit Windows build.")
endif()

set(DAHUA_SDK_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../NetSDK")

if(NOT EXISTS "${DAHUA_SDK_ROOT}/Include/Common/dhnetsdk.h")
    message(FATAL_ERROR "NetSDK headers were not found at ${DAHUA_SDK_ROOT}")
endif()

add_executable(dahua-events src/main.cpp)
target_compile_features(dahua-events PRIVATE cxx_std_17)
target_compile_definitions(dahua-events PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(dahua-events PRIVATE "${DAHUA_SDK_ROOT}/Include/Common")
target_link_libraries(dahua-events PRIVATE "${DAHUA_SDK_ROOT}/Lib/Win64/dhnetsdk.lib")

if(MSVC)
    target_compile_options(dahua-events PRIVATE /W4 /permissive- /EHsc)
endif()

add_custom_command(TARGET dahua-events POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${DAHUA_SDK_ROOT}/Bin"
            "$<TARGET_FILE_DIR:dahua-events>"
    COMMENT "Copying the official NetSDK runtime next to the experiment executable"
)
