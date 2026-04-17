import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import de from "@/locales/de.json";
import en from "@/locales/en.json";
import zh from "@/locales/zh.json";

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      de: { translation: de },
      en: { translation: en },
      zh: { translation: zh },  // #692 — LLM-initial, partial (Tier A0-...). Missing keys fall back to en.
    },
    fallbackLng: "en",  // #692: EN als Fallback, damit partielle ZH-Lokalisierung nicht halb leer wirkt.
    interpolation: { escapeValue: false },
    detection: { order: ["localStorage", "navigator"], caches: ["localStorage"] },
  });

export default i18n;
