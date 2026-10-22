/* rgbcore C API. Original file by EQQX2, 2026. GPL-3.0-or-later. */
#ifndef RGBCORE_H
#define RGBCORE_H
#ifdef __cplusplus
extern "C" {
#endif
#define RGBCORE_API __declspec(dllexport)
RGBCORE_API int         rgb_init(const char *config_dir);        /* detect devices (blocks); 0 = ok */
RGBCORE_API int         rgb_rescan(void);                         /* re-detect devices (blocks) */
RGBCORE_API void        rgb_shutdown(void);
RGBCORE_API const char *rgb_devices_json(void);                   /* device list as JSON (valid until next call) */
RGBCORE_API int         rgb_set_mode(int dev, const char *mode);  /* by mode name; 0 = ok */
RGBCORE_API int         rgb_set_colors(int dev, const unsigned int *rgb, int n); /* 0xRRGGBB per LED; applies */
RGBCORE_API int         rgb_resize_zone(int dev, int zone, int leds);
RGBCORE_API const char *rgb_core_version(void);
#ifdef __cplusplus
}
#endif
#endif
