/*---------------------------------------------------------*\
| rgbcore.cpp                                               |
|                                                           |
|   C API over OpenRGB's ResourceManager (device core).     |
|   Links against the unmodified OpenRGB sources in         |
|   third_party/openrgb, so it is a derived work of OpenRGB |
|   and is distributed under the GPL.                       |
|                                                           |
|   Original file by EQQX2, 2026                            |
|   SPDX-License-Identifier: GPL-3.0-or-later              |
\*---------------------------------------------------------*/
#include <chrono>
#include <future>
#include <mutex>
#include <thread>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
#include "ResourceManager.h"
#include "RGBController.h"
#include "rgbcore.h"
#include "wmi.h"

using json = nlohmann::json;

static std::mutex   g_mutex;
static std::string  g_json;
static bool         g_started = false;

static RGBController *dev_at(int i)
{
    std::vector<RGBController*> &c = ResourceManager::get()->GetRGBControllers();
    return (i >= 0 && i < (int)c.size()) ? c[i] : nullptr;
}

extern "C" {

RGBCORE_API int rgb_init(const char *config_dir)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    if(g_started) return 0;
    /* WMI (used by some detectors) is set up here, not in a static initialiser: COM inside DllMain deadlocks.
       The thread stays alive so the multithreaded COM apartment it creates is not torn down. */
    std::promise<void> wmi_ready;
    std::future<void> wmi_done = wmi_ready.get_future();
    std::thread([&wmi_ready]{ Wmi::init(); wmi_ready.set_value(); for(;;) std::this_thread::sleep_for(std::chrono::hours(24)); }).detach();
    wmi_done.wait();
    ResourceManager *rm = ResourceManager::get();
    if(rm == nullptr) rm = new ResourceManager();
    if(config_dir && *config_dir) rm->SetConfigurationDirectory(config_dir);
    /* tryConnect=false, detectDevices=true, startServer=false, applyPostOptions=false, startGui=false */
    rm->Initialize(false, true, false, false, false);
    rm->WaitForInitialization();
    rm->WaitForDetection();
    g_started = true;
    return 0;
}

RGBCORE_API int rgb_rescan(void)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    if(!g_started) return -1;
    ResourceManager::get()->RescanDevices();
    ResourceManager::get()->WaitForDetection();
    return 0;
}

RGBCORE_API void rgb_shutdown(void)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    if(!g_started) return;
    ResourceManager::get()->StopDeviceDetection();
    g_started = false;
}

RGBCORE_API const char *rgb_devices_json(void)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    json out = json::array();
    std::vector<RGBController*> &ctrls = ResourceManager::get()->GetRGBControllers();
    for(size_t i = 0; i < ctrls.size(); i++)
    {
        RGBController *c = ctrls[i];
        json d;
        d["index"]       = (int)i;
        d["name"]        = c->GetName();
        d["vendor"]      = c->GetVendor();
        d["description"] = c->GetDescription();
        d["location"]    = c->GetLocation();
        d["serial"]      = c->GetSerial();
        d["type"]        = (int)c->GetDeviceType();
        d["led_count"]   = c->GetLEDCount();
        d["active_mode"] = c->GetActiveMode();
        d["modes"]       = json::array();
        for(unsigned int m = 0; m < c->GetModeCount(); m++) d["modes"].push_back(c->GetModeName(m));
        d["zones"]       = json::array();
        for(unsigned int z = 0; z < c->GetZoneCount(); z++)
        {
            json zj;
            zj["name"] = c->GetZoneName(z); zj["type"] = (int)c->GetZoneType(z);
            zj["leds"] = c->GetZoneLEDsCount(z); zj["leds_min"] = c->GetZoneLEDsMin(z); zj["leds_max"] = c->GetZoneLEDsMax(z);
            d["zones"].push_back(zj);
        }
        out.push_back(d);
    }
    g_json = out.dump();
    return g_json.c_str();
}

RGBCORE_API int rgb_set_mode(int dev, const char *mode)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    RGBController *c = dev_at(dev);
    if(!c || !mode) return -1;
    for(unsigned int m = 0; m < c->GetModeCount(); m++)
    {
        if(c->GetModeName(m) == mode) { c->SetActiveMode((int)m); return 0; }
    }
    return -2; /* no such mode */
}

RGBCORE_API int rgb_set_colors(int dev, const unsigned int *rgb, int n)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    RGBController *c = dev_at(dev);
    if(!c || !rgb) return -1;
    unsigned int count = c->GetLEDCount();
    for(unsigned int i = 0; i < count && (int)i < n; i++)
        c->SetColor(i, ToRGBColor((rgb[i] >> 16) & 0xFF, (rgb[i] >> 8) & 0xFF, rgb[i] & 0xFF));
    c->UpdateLEDs();
    return 0;
}

RGBCORE_API int rgb_resize_zone(int dev, int zone, int leds)
{
    std::lock_guard<std::mutex> lock(g_mutex);
    RGBController *c = dev_at(dev);
    if(!c || zone < 0 || zone >= (int)c->GetZoneCount()) return -1;
    c->ResizeZone(zone, leds);
    return 0;
}

RGBCORE_API const char *rgb_core_version(void)
{
    return "rgbcore 0.1 / OpenRGB release_1.0 (81bbe18a84c2)";
}

}
