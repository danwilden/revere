import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi'

const medallionDark = {
  dark: true,
  colors: {
    background: '#0a0a0a',
    surface: '#111111',
    'surface-bright': '#1a1a1a',
    'surface-variant': '#1f1f1f',
    'on-surface-variant': '#cccccc',
    primary: '#FFE600',
    'primary-darken-1': '#ccb800',
    secondary: '#00FF41',
    'secondary-darken-1': '#00cc33',
    accent: '#FF6B00',
    error: '#FF2222',
    warning: '#FF6B00',
    info: '#4FC3F7',
    success: '#00FF41',
    'on-background': '#f0f0f0',
    'on-surface': '#f0f0f0',
    'on-primary': '#000000',
    'on-secondary': '#000000',
    'on-error': '#ffffff',
    'border-default': '#333333',
    'border-bright': '#555555',
    'text-muted': '#666666',
    'text-dim': '#444444',
  },
}

export default createVuetify({
  theme: {
    defaultTheme: 'medallionDark',
    themes: {
      medallionDark,
    },
  },
  icons: {
    defaultSet: 'mdi',
    aliases,
    sets: { mdi },
  },
  defaults: {
    VCard: {
      elevation: 0,
      border: true,
      rounded: 'none',
    },
    VBtn: {
      variant: 'outlined',
      rounded: 'none',
      elevation: 0,
    },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'none',
      hideDetails: 'auto',
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'none',
      hideDetails: 'auto',
    },
    VAutocomplete: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'none',
      hideDetails: 'auto',
    },
    VTextarea: {
      variant: 'outlined',
      density: 'comfortable',
      rounded: 'none',
      hideDetails: 'auto',
    },
    VChip: {
      rounded: 'none',
    },
    VAlert: {
      rounded: 'none',
    },
    VDataTable: {
      density: 'comfortable',
    },
    VDivider: {
      color: '#333333',
    },
    VList: {
      bgColor: 'transparent',
    },
    VListItem: {
      rounded: 'none',
    },
    VNavigationDrawer: {
      elevation: 0,
    },
    VTab: {
      rounded: 'none',
    },
  },
})
