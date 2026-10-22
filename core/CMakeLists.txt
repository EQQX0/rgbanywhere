# Build script for rgbcore.dll: OpenRGB's device core (pinned in ../third_party/openrgb) plus a small C API.
# Original file by EQQX2, 2026. GPL-3.0-or-later (see ../LICENSE). OpenRGB sources keep their own headers.
cmake_minimum_required(VERSION 3.20)
project(rgbcore CXX C)
set(CMAKE_CXX_STANDARD 17)
set(OR ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/openrgb)

# Controllers this app needs (see README / NOTICE). Add more directories here to support other hardware.
set(CONTROLLER_DIRS
    CorsairDRAMController
    AsusAuraUSBController
    LogitechController
)

set(CTRL_SRC "")
foreach(d ${CONTROLLER_DIRS})
    file(GLOB_RECURSE s CONFIGURE_DEPENDS ${OR}/Controllers/${d}/*.cpp)
    list(APPEND CTRL_SRC ${s})
endforeach()
# OpenRGB's suffix rule: a file ending _Linux/_MacOS/_FreeBSD (combinable) is for those OSes only; keep anything naming _Windows.
set(KEEP "")
foreach(f ${CTRL_SRC})
    if(f MATCHES "_Windows" OR NOT f MATCHES "_(Linux|MacOS|FreeBSD)[A-Za-z_]*\.cpp$")
        list(APPEND KEEP ${f})
    endif()
endforeach()
set(CTRL_SRC ${KEEP})

set(CORE_SRC
    ${OR}/DetectionManager.cpp ${OR}/JsonUtils.cpp ${OR}/LogManager.cpp ${OR}/MathUtils.cpp
    ${OR}/StringUtils.cpp ${OR}/NetworkClient.cpp ${OR}/NetworkProtocol.cpp ${OR}/NetworkServer.cpp
    ${OR}/ProfileManager.cpp ${OR}/ResourceManager.cpp ${OR}/SettingsManager.cpp ${OR}/cli.cpp
    ${OR}/dmiinfo/dmiinfo.cpp ${OR}/wmi/wmi.cpp
    ${OR}/SPDAccessor/DDR4DirectAccessor.cpp ${OR}/SPDAccessor/DDR5DirectAccessor.cpp
    ${OR}/SPDAccessor/SPDAccessor.cpp ${OR}/SPDAccessor/SPDDetector.cpp ${OR}/SPDAccessor/SPDWrapper.cpp
    ${OR}/i2c_smbus/i2c_smbus.cpp ${OR}/i2c_smbus/Windows/i2c_smbus_pawnio.cpp
    ${OR}/i2c_tools/i2c_tools.cpp
    ${OR}/interop/DeviceGuard.cpp ${OR}/interop/DeviceGuardLock.cpp ${OR}/interop/DeviceGuardManager.cpp
    ${OR}/net_port/net_port.cpp ${OR}/serial_port/serial_port.cpp ${OR}/serial_port/find_usb_serial_port_win.cpp
    ${OR}/super_io/super_io_pawnio.cpp
    ${OR}/AutoStart/AutoStart.cpp ${OR}/AutoStart/AutoStart-Windows.cpp
    ${OR}/KeyboardLayoutManager/KeyboardLayoutManager.cpp
    ${OR}/RGBController/RGBController.cpp ${OR}/RGBController/RGBController_Dummy.cpp
    ${OR}/RGBController/RGBControllerKeyNames.cpp ${OR}/RGBController/RGBController_Network.cpp
    ${OR}/RGBController/RGBController_Virtual.cpp
)

add_library(rgbcore SHARED ${CORE_SRC} ${CTRL_SRC} rgbcore.cpp)
target_compile_definitions(rgbcore PRIVATE
    VERSION_STRING="1.0" BUILDDATE_STRING="rgbcore" GIT_COMMIT_ID="81bbe18a84c2e507006f19dd252e397e40a56bfe"
    GIT_COMMIT_DATE="2026-09-11" GIT_BRANCH="release_1.0"
    WIN32 _MBCS WIN32_LEAN_AND_MEAN HID_HOTPLUG_ENABLED=1 _WIN32_WINNT=0x0A00 NOMINMAX RGBCORE_BUILD)
target_include_directories(rgbcore PRIVATE
    ${OR} ${OR}/RGBController ${OR}/i2c_smbus ${OR}/i2c_smbus/Windows ${OR}/i2c_tools ${OR}/interop ${OR}/hidapi_wrapper
    ${OR}/dmiinfo ${OR}/net_port ${OR}/pci_ids ${OR}/scsiapi ${OR}/serial_port ${OR}/super_io ${OR}/AutoStart
    ${OR}/KeyboardLayoutManager ${OR}/SPDAccessor ${OR}/startup ${OR}/wmi ${OR}/SuspendResume
    ${OR}/dependencies/json ${OR}/dependencies/httplib ${OR}/dependencies/CRCpp ${OR}/dependencies/stb
    ${OR}/dependencies/PawnIO ${OR}/dependencies/hidapi-hotplug-win/include ${OR}/dependencies/libusb-1.0.27/include
    ${OR}/dependencies/mdns ${OR}/dependencies/hueplusplus-1.2.0/include ${OR}/dependencies/libe131/src)
foreach(d ${CONTROLLER_DIRS})
    file(GLOB_RECURSE hs ${OR}/Controllers/${d}/*.h)
    foreach(h ${hs})
        get_filename_component(dir ${h} DIRECTORY)
        target_include_directories(rgbcore PRIVATE ${dir})
    endforeach()
endforeach()
target_link_libraries(rgbcore PRIVATE ws2_32 iphlpapi ole32 oleaut32 setupapi advapi32 user32 wbemuuid
    ${OR}/dependencies/PawnIO/PawnIOLib.dll
    ${OR}/dependencies/hidapi-hotplug-win/x64/hidapi-hotplug.dll
    ${OR}/dependencies/libusb-1.0.27/VS2019/MS64/dll/libusb-1.0.dll)
target_link_options(rgbcore PRIVATE -static-libgcc -static-libstdc++)
set_target_properties(rgbcore PROPERTIES PREFIX "")
