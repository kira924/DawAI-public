import { ArrowLeft, ArrowRight, Languages, LockKeyhole, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiError, normalizeApiError } from "../../api/client";
import { useAuth } from "../../auth/useAuth";
import { BrandMark } from "../../components/BrandMark";
import { useI18n } from "../../i18n/useI18n";

export function LoginPage() {
  const { login } = useAuth();
  const { locale, t, toggleLocale } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setError(t("invalidCredentials"));
      } else {
        const key = normalizeApiError(caught);
        setError(key === "network_unavailable" || key === "unexpected_error" ? t(key) : key);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const Arrow = locale === "ar" ? ArrowLeft : ArrowRight;

  return (
    <main className="login-page">
      <section className="login-story" aria-label={t("brandTagline")}>
        <div className="login-story-inner">
          <BrandMark />
          <div className="story-copy">
            <span className="eyebrow">{t("brandTagline")}</span>
            <h1>{locale === "ar" ? "كل وردية أوضح. كل قرار أسرع." : "Clearer shifts. Faster decisions."}</h1>
            <p>
              {locale === "ar"
                ? "مساحة موحدة للمبيعات والمخزون ويوم العمل، مصممة لإيقاع الصيدلية الحقيقي."
                : "One workspace for sales, stock, and the business day—built for the real pace of a pharmacy."}
            </p>
          </div>
          <div className="story-card">
            <div className="pulse-orbit"><span /></div>
            <div>
              <strong>{locale === "ar" ? "جاهز لنقطة البيع" : "Ready for the counter"}</strong>
              <small>{locale === "ar" ? "بحث سريع • صلاحية واضحة • وردية منضبطة" : "Fast search • Clear expiry • Controlled shifts"}</small>
            </div>
          </div>
        </div>
      </section>

      <section className="login-panel">
        <button className="language-button" type="button" onClick={toggleLocale}>
          <Languages size={18} />
          {t("language")}
        </button>
        <div className="login-form-wrap">
          <div className="mobile-brand"><BrandMark /></div>
          <div className="form-heading">
            <span className="icon-chip"><LockKeyhole size={20} /></span>
            <h2>{t("loginTitle")}</h2>
            <p>{t("loginSubtitle")}</p>
          </div>

          <form onSubmit={handleSubmit} className="login-form">
            <label>
              <span>{t("email")}</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
                required
                autoFocus
              />
            </label>
            <label>
              <span>{t("password")}</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            {error && <div className="alert alert-error" role="alert">{error}</div>}

            <button className="button button-primary button-large" type="submit" disabled={submitting}>
              <span>{submitting ? t("loggingIn") : t("login")}</span>
              {!submitting && <Arrow size={19} />}
            </button>
          </form>

          <p className="login-hint">{t("demoHint")}</p>
          <div className="secure-note">
            <ShieldCheck size={18} />
            <span>{t("secureSession")}</span>
          </div>
        </div>
      </section>
    </main>
  );
}
