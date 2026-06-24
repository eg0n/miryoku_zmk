// Copyright 2021 Manna Harbour
// https://github.com/manna-harbour/miryoku

// ── RGB LED indicator behaviors (connection / battery) ───────────────────
// Enable by passing dts_extra_cppflags: RGBLED_WIDGET_BEHAVIORS
// in the GitHub Actions workflow dispatch.
#if defined (RGBLED_WIDGET_BEHAVIORS)
#include <behaviors/rgbled_widget.dtsi>
#define IND_CON &ind_con
#define IND_BAT &ind_bat
#else
#define IND_CON &none
#define IND_BAT &none
#endif

// ── TOTEM layout mapping ──────────────────────────────────────────────────
#define MIRYOKU_LAYOUTMAPPING_TOTEM( \
     K00, K01, K02, K03, K04,      K05, K06, K07, K08, K09, \
     K10, K11, K12, K13, K14,      K15, K16, K17, K18, K19, \
     K20, K21, K22, K23, K24,      K25, K26, K27, K28, K29, \
     N30, N31, K32, K33, K34,      K35, K36, K37, N38, N39 \
) \
             K00  K01  K02  K03  K04       K05  K06  K07  K08  K09 \
             K10  K11  K12  K13  K14       K15  K16  K17  K18  K19 \
    IND_CON  K20  K21  K22  K23  K24       K25  K26  K27  K28  K29  IND_BAT \
                       K32  K33  K34       K35  K36  K37

// ── Dongle screen brightness behaviors ───────────────────────────────────
// Enable by passing dts_extra_cppflags: DONGLE_SCREEN_BRIGHTNESS_BEHAVIORS
// in the GitHub Actions workflow dispatch.
#if defined (DONGLE_SCREEN_BRIGHTNESS_BEHAVIORS)
#include <behaviors/dongle_screen_bl.dtsi>
#include <dt-bindings/zmk/dongle_screen_bl.h>
#define MIRYOKU_LAYER_MEDIA \
U_BOOT,            &u_to_U_TAP,       &u_to_U_EXTRA,     &u_to_U_BASE,      U_NA,              &ds_bl DS_BL_TOG,  U_RGB_EFF,         U_RGB_HUI,         U_RGB_SAI,         U_RGB_BRI,         \
&kp LGUI,          &kp LALT,          &kp LCTRL,         &kp LSHFT,         U_NA,              U_EP_TOG,          &kp C_PREV,        &kp C_VOL_DN,      &kp C_VOL_UP,      &kp C_NEXT,        \
U_NA,              &kp RALT,          &u_to_U_FUN,       &u_to_U_MEDIA,     U_NA,              &u_out_tog,        &u_bt_sel_0,       &u_bt_sel_1,       &u_bt_sel_2,       &u_bt_sel_3,       \
U_NP,              U_NP,              U_NA,              U_NA,              U_NA,              &kp C_STOP,        &kp C_PP,          &kp C_MUTE,        U_NP,              U_NP
#endif
