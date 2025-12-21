/**
 * Seeed Warehouse Design System - Tailwind CSS Preset
 *
 * This preset can be reused in other projects to maintain consistent design.
 *
 * Usage in tailwind.config.js:
 * import preset from './design-system/tailwind.preset.js'
 * export default { presets: [preset], content: [...] }
 */

export default {
  theme: {
    extend: {
      // Color System
      colors: {
        // Brand Colors
        primary: {
          DEFAULT: '#8cc63f',
          hover: '#7ab32f',
          light: '#e8f5d6',
        },
        secondary: '#2c2c2c',

        // Semantic Colors
        success: '#52c41a',
        warning: '#faad14',
        danger: '#ff4d4f',
        info: '#1890ff',

        // Background Colors
        body: '#f4f7f0',
        card: '#ffffff',
        sidebar: '#ffffff',
        header: '#e9f0e1',
        hover: '#f5f7fa',
        table: '#fafafa',

        // Text Colors
        'text-primary': '#1f1f1f',
        'text-secondary': '#666666',
        'text-muted': '#999999',

        // Border Colors
        border: {
          DEFAULT: '#e8e8e8',
          light: '#f0f0f0',
        },
      },

      // Border Radius System
      borderRadius: {
        none: '0px',
        sm: '4px',
        DEFAULT: '6px',
        md: '8px',
        lg: '12px',
        full: '9999px',
      },

      // Shadow System
      boxShadow: {
        sm: '0 2px 8px rgba(0, 0, 0, 0.05)',
        md: '0 4px 16px rgba(0, 0, 0, 0.08)',
        lg: '0 4px 16px rgba(0, 0, 0, 0.08)',
        focus: '0 0 0 2px rgba(140, 198, 63, 0.2)',
      },

      // Typography System
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },

      fontSize: {
        '2xs': ['11px', { lineHeight: '1.5' }],
        xs: ['12px', { lineHeight: '1.5' }],
        '13': ['13px', { lineHeight: '1.5' }],
        sm: ['14px', { lineHeight: '1.5' }],
        base: ['16px', { lineHeight: '1.5' }],
        lg: ['18px', { lineHeight: '1.5' }],
        xl: ['20px', { lineHeight: '1.5' }],
        '3xl': ['32px', { lineHeight: '1.2' }],
      },

      // Spacing System (based on 4px grid)
      spacing: {
        sidebar: '240px',
        header: '64px',
        '4.5': '18px',
        '5.5': '22px',
      },

      // Animation System
      transitionDuration: {
        DEFAULT: '200ms',
      },

      // Z-index System
      zIndex: {
        dropdown: '100',
        sticky: '200',
        modal: '1000',
        tooltip: '1100',
      },
    },
  },
}
