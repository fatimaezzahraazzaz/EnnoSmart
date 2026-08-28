"use client"

import { FormEvent, useEffect, useMemo, useState } from "react"
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  FilePenLine,
  Loader2,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles,
} from "lucide-react"

import {
  forgotPassword,
  getMe,
  login,
  register,
  resetPassword,
  type UserRead,
} from "@/lib/api"

type AuthMode = "login" | "register" | "forgot" | "reset"

interface LoginPageProps {
  onLogin: (user: UserRead) => void
}

const features = [
  {
    icon: Activity,
    title: "EnnoDiagnostic",
    description: "Analyse & cadrage du périmètre CIR",
  },
  {
    icon: BookOpen,
    title: "EnnoScholar",
    description: "Recherche & preuves de l’état de l’art",
  },
  {
    icon: Sparkles,
    title: "EnnoAmélioration",
    description: "Rédaction & amélioration continue",
  },
  {
    icon: Database,
    title: "Mémoire de connaissances",
    description: "Capitalisation sécurisée & réutilisable",
  },
]

const nodes = [
  {
    icon: Activity,
    title: "Verrous",
    description: "Identifier & lier les verrous techniques",
    position: "tl",
  },
  {
    icon: BookOpen,
    title: "Preuves",
    description: "Collecter & structurer les preuves R&D",
    position: "tr",
  },
  {
    icon: FilePenLine,
    title: "Articles",
    description: "Rechercher l’état de l’art pertinent",
    position: "bl",
  },
  {
    icon: Database,
    title: "Mémoire",
    description: "Capitaliser & réutiliser la connaissance",
    position: "br",
  },
]

function LandingOrb({
  onOpen,
}: {
  onOpen: () => void
}) {
  const sparks = Array.from({ length: 42 }, (_, i) => ({
    left: `${5 + ((i * 37) % 90)}%`,
    top: `${4 + ((i * 53) % 90)}%`,
    delay: `${(i % 8) * 0.4}s`,
  }))

  return (
    <section className="ennoma-login-cosmos" aria-label="Orchestration du dossier CIR">
      <div className="ennoma-login-halo" />

      <div className="ennoma-login-orbit ennoma-login-orbit-a">
        <i />
      </div>

      <div className="ennoma-login-orbit ennoma-login-orbit-b">
        <i />
      </div>

      <div className="ennoma-login-orbit ennoma-login-orbit-c">
        <i />
      </div>

      <div className="ennoma-login-orbit ennoma-login-orbit-d">
        <i />
      </div>

      <div className="ennoma-login-dots" />

      <div className="ennoma-login-low-gold" />
      <div className="ennoma-login-mid-gold" />

      {sparks.map((spark, index) => (
        <i
          className="ennoma-login-spark"
          key={index}
          style={{
            left: spark.left,
            top: spark.top,
            animationDelay: spark.delay,
          }}
        />
      ))}

      {nodes.map(({ icon: Icon, title, description, position }) => (
        <div
          className={`ennoma-login-node ennoma-login-node-${position}`}
          key={title}
        >
          <span>
            <Icon className="ennoma-login-node-icon" strokeWidth={1.8} />
          </span>

          <div>
            <strong>{title}</strong>
            <small>{description}</small>
          </div>
        </div>
      ))}

      <div className="ennoma-login-sphere">
        <div>
          <FilePenLine className="ennoma-login-sphere-icon" strokeWidth={1.7} />
          <strong>DOSSIER CIR</strong>
          <em>Noyau d’orchestration</em>
          <small>Traçable · Fiable · Conforme</small>
        </div>
      </div>

      <button
        type="button"
        className="ennoma-login-open"
        onClick={onOpen}
      >
        <ArrowUpRight className="size-4" strokeWidth={1.9} />
        Ouvrir l’espace
      </button>
    </section>
  )
}

function PasswordField({
  id,
  value,
  onChange,
  placeholder = "8 caractères minimum",
  autoComplete,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  autoComplete?: string
}) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="ennoma-auth-field">
      <LockKeyhole className="size-4 shrink-0" />
      <input
        id={id}
        type={visible ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        minLength={8}
        required
      />

      <button
        type="button"
        className="ennoma-auth-eye"
        onClick={() => setVisible((value) => !value)}
        aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  )
}

function AuthPanel({
  onLogin,
}: {
  onLogin: (user: UserRead) => void
}) {
  const [mode, setMode] = useState<AuthMode>("login")
  const [fullName, setFullName] = useState("")
  const [company, setCompany] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [resetToken, setResetToken] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("reset_token")
    if (token) {
      setResetToken(token)
      setMode("reset")
    }
  }, [])

  const passwordScore = useMemo(() => {
    let score = 0
    if (password.length >= 8) score += 1
    if (/[A-Z]/.test(password) && /[a-z]/.test(password)) score += 1
    if (/\d/.test(password)) score += 1
    if (/[^A-Za-z0-9]/.test(password)) score += 1
    return score
  }, [password])

  const switchMode = (next: AuthMode) => {
    setMode(next)
    setError("")
    setSuccess("")
    setPassword("")
    setConfirmPassword("")
  }

  async function beginDirectPasswordReset() {
    const normalizedEmail = email.trim()

    setError("")
    setSuccess("")

    if (!normalizedEmail) {
      setError(
        "Saisissez d’abord votre adresse e-mail dans le formulaire de connexion.",
      )
      return
    }

    setLoading(true)

    try {
      const response = await forgotPassword(normalizedEmail)

      // En local / développement, le backend renvoie un jeton temporaire
      // à usage unique. On saute l'ancien écran « Envoyer le lien ».
      if (response.preview_token) {
        setResetToken(response.preview_token)
        setPassword("")
        setConfirmPassword("")
        setMode("reset")
        return
      }

      // En production le jeton n'est volontairement jamais exposé
      // au navigateur : on conserve la sécurité du flux par e-mail.
      setSuccess(response.message)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de démarrer la réinitialisation.",
      )
    } finally {
      setLoading(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError("")
    setSuccess("")

    try {
      if (mode === "login") {
        await login({ email, password })
        onLogin(await getMe())
        return
      }

      if (mode === "register") {
        if (password !== confirmPassword) {
          throw new Error("Les mots de passe ne correspondent pas.")
        }

        await register({
          full_name: fullName,
          email,
          password,
          company: company || undefined,
          job_title: "Consultant CIR",
        })

        await login({ email, password })
        onLogin(await getMe())
        return
      }

      if (mode === "forgot") {
        const response = await forgotPassword(email)
        setSuccess(response.message)

        if (response.preview_token) {
          setResetToken(response.preview_token)
          setTimeout(() => switchMode("reset"), 900)
        }
        return
      }

      if (!resetToken) {
        throw new Error("Le jeton de réinitialisation est absent.")
      }

      if (password !== confirmPassword) {
        throw new Error("Les mots de passe ne correspondent pas.")
      }

      const response = await resetPassword(resetToken, password)
      window.history.replaceState({}, "", window.location.pathname)
      setSuccess(response.message)

      setTimeout(() => switchMode("login"), 1000)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Une erreur inattendue est survenue.",
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="ennoma-auth-card">
      {(mode === "login" || mode === "register") && (
        <div className="ennoma-auth-tabs">
          <button
            type="button"
            className={mode === "login" ? "on" : ""}
            onClick={() => switchMode("login")}
          >
            Connexion
          </button>

          <button
            type="button"
            className={mode === "register" ? "on" : ""}
            onClick={() => switchMode("register")}
          >
            Inscription
          </button>
        </div>
      )}

      {mode === "reset" && (
        <button
          type="button"
          className="ennoma-auth-back"
          onClick={() => switchMode("login")}
        >
          ← Retour à la connexion
        </button>
      )}

      <h2>
        {mode === "login"
          ? "Bienvenue sur Ennoma"
          : mode === "register"
            ? "Créer votre compte"
            : mode === "forgot"
              ? "Mot de passe oublié"
              : "Nouveau mot de passe"}
      </h2>

      <p>
        {mode === "login"
          ? "Connectez-vous à votre espace de travail."
          : mode === "register"
            ? "Rejoignez l’espace interne sécurisé Ennoma."
            : mode === "forgot"
              ? "Recevez un lien de récupération sécurisé."
              : "Définissez un nouveau mot de passe."}
      </p>

      {error && (
        <div className="ennoma-auth-message ennoma-auth-error">
          <AlertCircle className="size-4 shrink-0" />
          {error}
        </div>
      )}

      {success && (
        <div className="ennoma-auth-message ennoma-auth-success">
          <CheckCircle2 className="size-4 shrink-0" />
          {success}
        </div>
      )}

      <form onSubmit={submit}>
        {mode === "register" && (
          <>
            <label>
              Nom complet
              <div className="ennoma-auth-field">
                <input
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  placeholder="Prénom Nom"
                  autoComplete="name"
                  required
                />
              </div>
            </label>

            <label>
              Cabinet ou entreprise
              <div className="ennoma-auth-field">
                <input
                  value={company}
                  onChange={(event) => setCompany(event.target.value)}
                  placeholder="Votre organisation"
                  autoComplete="organization"
                />
              </div>
            </label>
          </>
        )}

        {mode !== "reset" && (
          <label>
            Adresse e-mail
            <div className="ennoma-auth-field">
              <Mail className="size-4 shrink-0" />
              <input
                id="email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="vous@ennoma.fr"
                autoComplete="email"
                required
              />
            </div>
          </label>
        )}

        {(mode === "login" || mode === "register" || mode === "reset") && (
          <label>
            <span>Mot de passe</span>

            {mode === "login" && (
              <button
                type="button"
                className="ennoma-auth-forgot"
                onClick={() => void beginDirectPasswordReset()}
                disabled={loading}
              >
                Mot de passe oublié ?
              </button>
            )}

            <PasswordField
              id="password"
              value={password}
              onChange={setPassword}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />

            {(mode === "register" || mode === "reset") && password && (
              <div className="ennoma-password-score">
                {[1, 2, 3, 4].map((level) => (
                  <span
                    key={level}
                    className={passwordScore >= level ? "on" : ""}
                  />
                ))}
              </div>
            )}
          </label>
        )}

        {(mode === "register" || mode === "reset") && (
          <label>
            Confirmer le mot de passe
            <PasswordField
              id="confirm-password"
              value={confirmPassword}
              onChange={setConfirmPassword}
              autoComplete="new-password"
            />
          </label>
        )}

        {mode === "reset" && !resetToken && (
          <label>
            Jeton de récupération
            <div className="ennoma-auth-field">
              <input
                value={resetToken}
                onChange={(event) => setResetToken(event.target.value)}
                placeholder="Collez le jeton reçu"
                required
              />
            </div>
          </label>
        )}

        <button
          className="ennoma-auth-submit"
          type="submit"
          disabled={loading}
        >
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Traitement…
            </>
          ) : (
            <>
              <LockKeyhole className="size-4" />
              {mode === "login"
                ? "Se connecter"
                : mode === "register"
                  ? "Créer mon compte"
                  : mode === "forgot"
                    ? "Envoyer le lien"
                    : "Enregistrer"}
              <span>→</span>
            </>
          )}
        </button>
      </form>

      <div className="ennoma-auth-secure">
        <ShieldCheck className="size-4" />
        Connexion sécurisée
        <small>
          Vos données restent protégées dans un espace chiffré et contrôlé par rôle.
        </small>
      </div>
    </section>
  )
}

export default function LoginPage({
  onLogin,
}: LoginPageProps) {
  const [authOpen, setAuthOpen] = useState(false)

  return (
    <main className="ennoma-login-page">
      <div className="ennoma-login-ambient" />

      <header className="ennoma-login-header">
        <div className="ennoma-login-brand">
          <img
            src="/ennoma-logo.png"
            alt="Logo Ennoma"
            className="ennoma-login-brand-logo"
          />

          <div className="ennoma-login-brand-copy">
            <strong>Ennoma</strong>
            <span>EnnoSmart · Plateforme CIR multi-agents</span>
          </div>
        </div>

        <div className="ennoma-login-private">
          <LockKeyhole className="size-4" />
          Accès réservé aux équipes internes
        </div>
      </header>

      <div className="ennoma-login-layout">
        <section className="ennoma-login-intro">
          <div className="ennoma-login-eyebrow">PLATEFORME INTERNE</div>

          <h1>
            Votre intelligence
            <br />
            CIR, <span>orchestrée.</span>
          </h1>

          <p>
            Ennoma centralise vos agents CIR et votre mémoire de connaissances
            pour produire des dossiers fiables, traçables et conformes, en toute maîtrise.
          </p>

          <div className="ennoma-login-features">
            {features.map(({ icon: Icon, title, description }) => (
              <div className="ennoma-login-feature" key={title}>
                <span>
                  <Icon className="ennoma-login-feature-icon" strokeWidth={1.8} />
                </span>

                <div>
                  <strong>{title}</strong>
                  <small>{description}</small>
                </div>
              </div>
            ))}
          </div>
        </section>

        <LandingOrb onOpen={() => setAuthOpen(true)} />
      </div>

      {authOpen && (
        <div className="ennoma-login-auth-overlay">
          <button
            type="button"
            className="ennoma-login-auth-backdrop"
            onClick={() => setAuthOpen(false)}
            aria-label="Fermer la connexion"
          />

          <div className="ennoma-login-auth-modal">
            <AuthPanel onLogin={onLogin} />
          </div>
        </div>
      )}

      <style jsx global>{`
        .ennoma-login-page {
          position: relative;
          min-height: 100dvh;
          overflow: hidden;
          color: #17131d;
          background:
            radial-gradient(
              circle at 52% 46%,
              rgba(255,255,255,.96) 0%,
              rgba(248,245,255,.93) 27%,
              rgba(235,229,250,.94) 52%,
              rgba(218,210,241,.96) 76%,
              rgba(205,197,231,.98) 100%
            ),
            linear-gradient(
              112deg,
              #fbfaff 0%,
              #f4f0fc 32%,
              #e7e0f6 65%,
              #d8d0ec 100%
            );
        }

        .ennoma-login-ambient {
          position: absolute;
          inset: 0;
          overflow: hidden;
          background:
            radial-gradient(
              circle at 53% 46%,
              rgba(159,126,236,.20) 0%,
              rgba(142,108,223,.13) 23%,
              rgba(118,86,194,.075) 41%,
              transparent 59%
            ),
            radial-gradient(
              circle at 70% 26%,
              rgba(206,190,251,.24) 0%,
              transparent 35%
            ),
            radial-gradient(
              circle at 42% 88%,
              rgba(112,80,190,.17) 0%,
              transparent 42%
            ),
            linear-gradient(
              90deg,
              rgba(255,255,255,.35),
              rgba(219,212,239,.18)
            );
          animation: ennoma-login-ambient 10s ease-in-out infinite alternate;
        }

        .ennoma-login-ambient::before {
          content: "";
          position: absolute;
          inset: 0;
          opacity: .20;
          background-image:
            radial-gradient(
              circle at 1px 1px,
              rgba(108,79,167,.13) 1px,
              transparent 1.1px
            );
          background-size: 31px 31px;
          mask-image: linear-gradient(to right, transparent 0%, black 35%, black 100%);
          -webkit-mask-image: linear-gradient(to right, transparent 0%, black 35%, black 100%);
        }

        .ennoma-login-ambient::after {
          content: "";
          position: absolute;
          left: 20%;
          right: 8%;
          bottom: -18%;
          height: 44%;
          border-radius: 50%;
          background:
            linear-gradient(
              90deg,
              rgba(154,128,229,.12),
              rgba(194,169,247,.08),
              transparent 72%
            );
          filter: blur(44px);
          transform: rotate(-5deg);
        }

        .ennoma-login-header {
          position: relative;
          z-index: 10;
          height: 86px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 3%;
        }

        .ennoma-login-brand {
          display: flex;
          align-items: center;
          gap: 13px;
        }

        .ennoma-login-brand-logo {
          width: 50px;
          height: 50px;
          border-radius: 15px;
          object-fit: cover;
          background: white;
          box-shadow:
            0 12px 28px rgba(89,63,171,.18),
            0 0 0 1px rgba(255,255,255,.9);
        }

        .ennoma-login-brand-copy {
          display: flex;
          flex-direction: column;
          justify-content: center;
        }

        .ennoma-login-brand-copy strong {
          font-size: 22px;
          font-weight: 800;
          line-height: 1.05;
          color: #17131d;
          letter-spacing: -.025em;
        }

        .ennoma-login-brand-copy span {
          margin-top: 4px;
          font-size: 11px;
          font-weight: 500;
          color: #746d7d;
        }

        .ennoma-login-private {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 11px 19px;
          border: 1px solid rgba(255,255,255,.72);
          border-radius: 15px;
          background: rgba(255,255,255,.57);
          color: #5e5867;
          font-size: 11px;
          box-shadow:
            0 10px 26px rgba(91,71,139,.08),
            inset 0 1px 0 rgba(255,255,255,.86);
        }

        .ennoma-login-layout {
          position: relative;
          z-index: 2;
          min-height: calc(100dvh - 86px);
          display: grid;
          grid-template-columns: minmax(360px,.72fr) minmax(620px,1.28fr);
          align-items: center;
          gap: 2%;
          padding: 15px 4% 35px;
        }

        .ennoma-login-intro {
          max-width: 470px;
        }

        .ennoma-login-eyebrow {
          color: #6940c3;
          font-size: 10px;
          font-weight: 800;
          letter-spacing: .09em;
        }

        .ennoma-login-intro h1 {
          margin: 30px 0 22px;
          font-size: clamp(44px,3.45vw,59px);
          line-height: 1.02;
          letter-spacing: -.05em;
        }

        .ennoma-login-intro h1 span {
          color: #6732c8;
        }

        .ennoma-login-intro > p {
          max-width: 440px;
          margin: 0;
          color: #635e6a;
          font-size: 13px;
          line-height: 1.75;
        }

        .ennoma-login-features {
          display: grid;
          gap: 15px;
          margin-top: 27px;
        }

        .ennoma-login-feature {
          display: flex;
          align-items: center;
          gap: 13px;
        }

        .ennoma-login-feature > span,
        .ennoma-login-node > span {
          display: grid;
          place-items: center;
          border-radius: 50%;
          background: rgba(255,255,255,.80);
          color: #6633ca;
          box-shadow: 0 8px 22px rgba(87,65,126,.09);
        }

        .ennoma-login-feature > span {
          width: 40px;
          height: 40px;
          border: 1px solid rgba(136,105,220,.13);
          box-shadow:
            0 8px 22px rgba(87,65,126,.08),
            inset 0 1px 0 rgba(255,255,255,.88);
        }

        .ennoma-login-feature-icon {
          width: 18px;
          height: 18px;
        }

        .ennoma-login-node-icon {
          width: 20px;
          height: 20px;
        }

        .ennoma-login-sphere-icon {
          width: 24px;
          height: 24px;
          color: rgba(255,255,255,.94);
          filter: drop-shadow(0 0 8px rgba(255,255,255,.28));
        }

        .ennoma-login-feature strong,
        .ennoma-login-feature small {
          display: block;
        }

        .ennoma-login-feature strong {
          font-size: 12px;
        }

        .ennoma-login-feature small {
          margin-top: 4px;
          color: #6e6875;
          font-size: 10px;
        }

        .ennoma-login-cosmos {
          position: relative;
          height: 670px;
          display: grid;
          place-items: center;
          perspective: 1100px;
        }

        .ennoma-login-cosmos .ennoma-login-sphere {
          isolation: isolate;
        }

        .ennoma-login-cosmos .ennoma-login-sphere > div::after {
          content: "";
          position: absolute;
          left: 50%;
          top: calc(100% + 38px);
          width: 210px;
          height: 28px;
          transform: translateX(-50%);
          border-radius: 50%;
          background: rgba(79,52,147,.16);
          filter: blur(18px);
          z-index: -1;
        }

        .ennoma-login-cosmos::before,
        .ennoma-login-cosmos::after {
          content: "";
          position: absolute;
          z-index: 3;
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: rgba(255,248,225,.98);
          box-shadow:
            0 0 8px 3px rgba(255,248,225,.95),
            0 0 18px 7px rgba(255,224,176,.36);
          animation: ennoma-login-spark 4.2s ease-in-out infinite;
        }

        .ennoma-login-cosmos::before {
          left: 31%;
          top: 67%;
        }

        .ennoma-login-cosmos::after {
          right: 27%;
          top: 42%;
          animation-delay: 1.3s;
        }

        .ennoma-login-halo {
          position: absolute;
          width: 585px;
          height: 585px;
          border-radius: 50%;
          background:
            radial-gradient(
              circle,
              rgba(255,255,255,.64) 0%,
              rgba(221,205,255,.42) 21%,
              rgba(173,137,244,.26) 41%,
              rgba(126,89,211,.12) 57%,
              transparent 74%
            );
          filter: blur(30px);
          animation: ennoma-login-breathe 5.8s ease-in-out infinite;
        }

        .ennoma-login-orbit,
        .ennoma-login-dots {
          position: absolute;
          border-radius: 50%;
          transform-origin: center;
          pointer-events: none;
        }

        /* Grande orbite blanche */
        .ennoma-login-orbit-a {
          width: 525px;
          height: 258px;
          border: 1.15px solid rgba(255,255,255,.72);
          box-shadow:
            0 0 8px rgba(255,255,255,.36),
            0 0 15px rgba(221,211,255,.18);
          animation: ennoma-login-spin 21s linear infinite;
        }

        /* Orbite violette visible */
        .ennoma-login-orbit-b {
          width: 475px;
          height: 305px;
          border: 1px solid rgba(150,112,232,.42);
          transform: rotate(62deg);
          box-shadow: 0 0 8px rgba(137,98,226,.14);
          animation: ennoma-login-reverse 25s linear infinite;
        }

        /* Orbite lilas croisée */
        .ennoma-login-orbit-c {
          width: 515px;
          height: 270px;
          border: 1px solid rgba(205,187,251,.40);
          transform: rotate(-52deg);
          animation: ennoma-login-spin 30s linear infinite;
        }

        /* Orbite dorée inclinée */
        .ennoma-login-orbit-d {
          width: 485px;
          height: 365px;
          border: 1.2px solid rgba(255,222,166,.64);
          transform: rotate(17deg);
          box-shadow:
            0 0 8px rgba(255,226,177,.32),
            0 0 18px rgba(255,226,177,.14);
          animation: ennoma-login-reverse 33s linear infinite;
        }

        /* Points lumineux qui voyagent sur les orbites */
        .ennoma-login-orbit i {
          position: absolute;
          left: 8%;
          top: 12%;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: rgba(255,255,255,.98);
          box-shadow:
            0 0 8px 3px rgba(255,255,255,.98),
            0 0 16px 6px rgba(207,183,255,.36);
        }

        .ennoma-login-orbit-d i {
          background: #fff0ca;
          box-shadow:
            0 0 8px 3px rgba(255,244,215,.98),
            0 0 18px 7px rgba(255,213,145,.55);
        }

        /* Couronne extérieure en pointillés */
        .ennoma-login-dots {
          width: 555px;
          height: 555px;
          border: 1px dashed rgba(255,255,255,.38);
          opacity: .78;
          animation: ennoma-login-spin 38s linear infinite;
        }

        /* Ligne dorée qui traverse le milieu de la sphère */
        .ennoma-login-mid-gold {
          position: absolute;
          z-index: 3;
          width: 495px;
          height: 118px;
          border: 1.5px solid rgba(255,219,153,.78);
          border-radius: 50%;
          transform: rotate(-8deg);
          pointer-events: none;
          box-shadow:
            0 0 8px rgba(255,231,190,.48),
            0 0 18px rgba(255,221,168,.24);
          animation: ennoma-login-mid-orbit 18s linear infinite;
        }

        .ennoma-login-mid-gold::before,
        .ennoma-login-mid-gold::after {
          content: "";
          position: absolute;
          top: 50%;
          width: 9px;
          height: 9px;
          margin-top: -4.5px;
          border-radius: 50%;
          background: #fff4d8;
          box-shadow:
            0 0 8px 3px rgba(255,249,225,.96),
            0 0 20px 8px rgba(255,207,125,.65);
        }

        .ennoma-login-mid-gold::before {
          left: 21%;
        }

        .ennoma-login-mid-gold::after {
          right: 18%;
        }

        /* Seconde ligne dorée plus large, sous la boule */
        .ennoma-login-low-gold {
          position: absolute;
          z-index: 2;
          width: 475px;
          height: 205px;
          border: 1px solid rgba(255,221,170,.45);
          border-radius: 50%;
          transform: rotate(18deg);
          pointer-events: none;
          box-shadow: 0 0 10px rgba(255,224,174,.16);
          animation: ennoma-login-reverse 26s linear infinite;
        }

        .ennoma-login-sphere {
          position: absolute;
          width: 285px;
          height: 285px;
          overflow: hidden;
          border-radius: 50%;

          /* Couleurs plus proches de la référence :
             blanc/lilas en haut-gauche, violet lumineux au centre,
             profondeur violette en bas-droite */
          background:
            radial-gradient(
              circle at 31% 22%,
              rgba(255,255,255,1) 0%,
              rgba(248,244,255,1) 7%,
              rgba(226,213,255,.98) 17%,
              rgba(193,164,252,.97) 30%,
              rgba(151,111,238,.98) 46%,
              rgba(113,74,215,.99) 62%,
              rgba(83,49,173,1) 78%,
              rgba(49,24,111,1) 91%,
              rgba(30,13,71,1) 100%
            );

          box-shadow:
            inset -30px -27px 56px rgba(30,7,66,.48),
            inset 24px 18px 42px rgba(255,255,255,.52),
            0 0 0 1px rgba(255,255,255,.78),
            0 0 24px 5px rgba(255,255,255,.88),
            0 0 76px 20px rgba(147,108,239,.34),
            0 28px 62px rgba(68,40,145,.26),
            0 48px 90px rgba(73,47,143,.16);

          animation: ennoma-login-float 6s ease-in-out infinite;
        }

        /* Reflet principal très doux, comme une sphère vitrée */
        .ennoma-login-sphere::before {
          content: "";
          position: absolute;
          inset: 0;
          border-radius: 50%;
          background:
            radial-gradient(
              circle at 27% 21%,
              rgba(255,255,255,.88) 0%,
              rgba(255,255,255,.48) 13%,
              rgba(255,255,255,.15) 25%,
              transparent 39%
            ),
            linear-gradient(
              112deg,
              transparent 28%,
              rgba(255,255,255,.11) 46%,
              transparent 61%
            );
          mix-blend-mode: screen;
          animation: ennoma-login-spin 14s linear infinite;
        }

        /* Bord fin et lumineux au lieu d'une grosse arête blanche */
        .ennoma-login-sphere::after {
          content: "";
          position: absolute;
          inset: 0;
          border: 1px solid rgba(255,255,255,.48);
          border-radius: 50%;
          box-shadow:
            inset 0 0 18px rgba(255,255,255,.28),
            inset 10px 6px 18px rgba(255,255,255,.16);
        }

        .ennoma-login-sphere > div {
          position: absolute;
          z-index: 2;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-direction: column;
          color: #fff;
          text-align: center;
          text-shadow: 0 2px 12px #15052e;
        }

        .ennoma-login-sphere strong {
          margin-top: 9px;
          font-size: 17px;
        }

        .ennoma-login-sphere em {
          margin-top: 8px;
          font-size: 11px;
        }

        .ennoma-login-sphere small {
          margin-top: 7px;
          color: #e8defb;
          font-size: 10px;
        }

        .ennoma-login-spark {
          position: absolute;
          width: 2.7px;
          height: 2.7px;
          border-radius: 50%;
          background: rgba(255,255,255,.98);
          box-shadow:
            0 0 6px 2px rgba(255,255,255,.90),
            0 0 14px 5px rgba(176,145,238,.24);
          animation: ennoma-login-spark 4.9s ease-in-out infinite;
        }

        .ennoma-login-open {
          position: absolute;
          z-index: 4;
          bottom: 40px;
          display: inline-flex;
          align-items: center;
          gap: 9px;
          padding: 15px 27px;
          border: 1px solid #d8c8ff;
          border-radius: 30px;
          background: #fff;
          color: #5530ad;
          font-weight: 700;
          box-shadow:
            0 14px 32px rgba(86,58,157,.15),
            0 3px 8px rgba(68,43,132,.06);
          transition: .2s;
        }

        .ennoma-login-open:hover {
          transform: translateY(-2px);
          filter: brightness(1.03);
        }

        .ennoma-login-node {
          position: absolute;
          z-index: 3;
          display: flex;
          align-items: center;
          gap: 12px;
          animation: ennoma-login-node 5s ease-in-out infinite;
        }

        .ennoma-login-node > span {
          width: 56px;
          height: 56px;
        }

        .ennoma-login-node strong,
        .ennoma-login-node small {
          display: block;
        }

        .ennoma-login-node strong {
          font-size: 11px;
        }

        .ennoma-login-node small {
          width: 115px;
          margin-top: 4px;
          color: #5c5664;
          font-size: 9px;
          line-height: 1.5;
        }

        .ennoma-login-node-tl {
          left: 2%;
          top: 17%;
          flex-direction: row-reverse;
          text-align: right;
        }

        .ennoma-login-node-tr {
          right: 0;
          top: 17%;
        }

        .ennoma-login-node-bl {
          left: 0;
          bottom: 17%;
          flex-direction: row-reverse;
          text-align: right;
        }

        .ennoma-login-node-br {
          right: -2%;
          bottom: 17%;
        }

        .ennoma-login-auth-overlay {
          position: fixed;
          z-index: 100;
          inset: 0;
          display: grid;
          place-items: center;
          padding: 24px;
        }

        .ennoma-login-auth-backdrop {
          position: absolute;
          inset: 0;
          border: 0;
          background: rgba(247,244,255,.10);
          backdrop-filter: blur(5px);
        }

        .ennoma-login-auth-modal {
          position: relative;
          z-index: 2;
          width: min(470px,calc(100vw - 32px));
        }

        .ennoma-auth-card {
          width: 100%;
          padding: 27px;
          border: 1px solid rgba(255,255,255,.90);
          border-radius: 28px;
          background:
            linear-gradient(
              180deg,
              rgba(255,255,255,.90),
              rgba(252,250,255,.84)
            );
          box-shadow:
            0 28px 74px rgba(71,52,111,.13),
            0 8px 24px rgba(80,59,122,.06),
            inset 0 1px 0 rgba(255,255,255,.96);
          backdrop-filter: blur(26px);
        }

        .ennoma-auth-tabs {
          display: grid;
          grid-template-columns: 1fr 1fr;
          margin-bottom: 30px;
          border-bottom: 1px solid #e7e2ed;
        }

        .ennoma-auth-tabs button {
          position: relative;
          padding: 12px;
          border: 0;
          background: none;
          color: #6d6674;
          font-size: 12px;
        }

        .ennoma-auth-tabs button.on {
          color: #5d29c6;
          font-weight: 700;
        }

        .ennoma-auth-tabs button.on::after {
          content: "";
          position: absolute;
          right: 14px;
          bottom: -1px;
          left: 14px;
          height: 2px;
          background: #6231ca;
        }

        .ennoma-auth-card h2 {
          margin: 0 0 9px;
          font-size: 23px;
        }

        .ennoma-auth-card > p {
          margin: 0 0 27px;
          color: #6c6673;
          font-size: 12px;
        }

        .ennoma-auth-card form {
          display: grid;
          gap: 18px;
        }

        .ennoma-auth-card label {
          position: relative;
          font-size: 11px;
          font-weight: 700;
        }

        .ennoma-auth-forgot {
          float: right;
          border: 0;
          background: none;
          color: #6331c2;
          font-size: 10px;
        }

        .ennoma-auth-field {
          min-height: 52px;
          display: flex;
          align-items: center;
          margin-top: 10px;
          padding: 0 14px;
          border: 1px solid #ddd8e7;
          border-radius: 12px;
          background: #f4f3fa;
          color: #696270;
        }

        .ennoma-auth-field input {
          width: 100%;
          padding: 0 12px;
          border: 0;
          outline: 0;
          background: transparent;
          color: #302a37;
          font-size: 12px;
        }

        .ennoma-auth-eye {
          border: 0;
          background: none;
          color: #696270;
        }

        .ennoma-auth-submit {
          height: 52px;
          display: flex;
          align-items: center;
          gap: 9px;
          padding: 0 17px;
          border: 0;
          border-radius: 12px;
          background: linear-gradient(100deg,#6932d8,#4d24c5);
          color: #fff;
          font-size: 12px;
          font-weight: 700;
          text-align: left;
        }

        .ennoma-auth-submit > span {
          margin-left: auto;
        }

        .ennoma-auth-submit:disabled {
          cursor: not-allowed;
          opacity: .65;
        }

        .ennoma-auth-secure {
          margin-top: 25px;
          padding-top: 18px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 7px;
          border-top: 1px solid #e4dfe8;
          color: #625b6a;
          font-size: 10px;
          text-align: center;
          flex-wrap: wrap;
        }

        .ennoma-auth-secure small {
          display: block;
          flex-basis: 100%;
          margin-top: 2px;
          color: #89828e;
          font-size: 8px;
          line-height: 1.5;
        }

        .ennoma-auth-message {
          display: flex;
          align-items: flex-start;
          gap: 8px;
          margin-bottom: 16px;
          padding: 11px 12px;
          border-radius: 11px;
          font-size: 11px;
        }

        .ennoma-auth-error {
          color: #b42318;
          background: #fff1f0;
          border: 1px solid #ffd0cc;
        }

        .ennoma-auth-success {
          color: #067647;
          background: #ecfdf3;
          border: 1px solid #abefc6;
        }

        .ennoma-auth-back {
          margin-bottom: 18px;
          border: 0;
          background: none;
          color: #6331c2;
          font-size: 11px;
          font-weight: 700;
        }

        .ennoma-password-score {
          display: flex;
          gap: 5px;
          margin-top: 7px;
        }

        .ennoma-password-score span {
          height: 3px;
          flex: 1;
          border-radius: 999px;
          background: #e5e0ea;
        }

        .ennoma-password-score span.on {
          background: linear-gradient(90deg,#6932d8,#9c5cf3);
        }

        @keyframes ennoma-login-spin {
          to { transform: rotate(360deg); }
        }

        @keyframes ennoma-login-mid-orbit {
          0% {
            transform: rotate(-8deg);
          }
          50% {
            transform: rotate(-5deg);
          }
          100% {
            transform: rotate(-8deg);
          }
        }

        @keyframes ennoma-login-reverse {
          to { transform: rotate(-360deg); }
        }

        @keyframes ennoma-login-breathe {
          50% {
            transform: scale(1.08);
            opacity: .7;
          }
        }

        @keyframes ennoma-login-spark {
          0%,100% {
            opacity: .1;
            transform: scale(.35);
          }
          50% {
            opacity: 1;
            transform: scale(1.3);
          }
        }

        @keyframes ennoma-login-node {
          50% { transform: translateY(-8px); }
        }

        @keyframes ennoma-login-float {
          50% { transform: translateY(-7px) rotate(1deg); }
        }

        @keyframes ennoma-login-ambient {
          to { filter: hue-rotate(7deg); }
        }

        @media (max-width: 1150px) {
          .ennoma-login-layout {
            grid-template-columns: 1fr;
          }

          .ennoma-login-intro {
            margin-inline: auto;
            text-align: center;
          }

          .ennoma-login-cosmos {
            height: 560px;
          }

          .ennoma-login-node {
            display: none;
          }
        }

        @media (max-width: 760px) {
          .ennoma-login-header {
            padding: 0 20px;
          }

          .ennoma-login-private,
          .ennoma-login-brand-copy span {
            display: none;
          }

          .ennoma-login-layout {
            display: flex;
            flex-direction: column;
            padding: 20px;
          }

          .ennoma-login-intro h1 {
            font-size: 45px;
          }

          .ennoma-login-features {
            display: none;
          }

          .ennoma-login-cosmos {
            width: 100%;
            height: 500px;
            transform: scale(.84);
          }

          .ennoma-login-sphere {
            width: 230px;
            height: 230px;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .ennoma-login-page *,
          .ennoma-login-page *::before,
          .ennoma-login-page *::after {
            animation: none !important;
          }
        }
      `}</style>
    </main>
  )
}
