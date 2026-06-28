import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './en.json';
import ru from './ru.json';

// ru is the default and the fallback; en is next. All UI strings live here, not
// in the API (the API is locale-neutral — see context/frontend.md).
void i18n.use(initReactI18next).init({
  resources: {
    ru: { translation: ru },
    en: { translation: en },
  },
  lng: 'ru',
  fallbackLng: 'ru',
  interpolation: { escapeValue: false },
});

export { default as i18n } from 'i18next';
