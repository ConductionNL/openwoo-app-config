---
last_reviewed: 2026-08-07
owner: info@conduction.nl
---

# Branding van een omgeving

Alles wat de WOO-website toont: naam, thema, jumbotron, favicon, de eigen
host en het certificaat. Het adminwachtwoord hoort daar níét bij — dat is
Nextcloud, zie [secret-reveal.md](secret-reveal.md).

Portaal → **Branding** → kies een omgeving.

## Wat je kunt zetten

| Veld | Landt in het tenantbestand als |
|---|---|
| Weergavenaam organisatie | `frontend.branding.organisationName` |
| NL Design-thema | `frontend.branding.themeClassname` |
| Jumbotron-afbeelding | `frontend.branding.jumbotronImageUrl` |
| Favicon | `frontend.branding.faviconUrl` |
| Frontend-versie | `frontend.tag` |
| Eigen frontend-host | `frontend.host` |
| Certificaat | `frontend.tls.issuer` |

Indienen opent een PR op de tenants-repo. Na de merge rolt Argo hem uit.

## Twee dingen die verrassen

**Huisstijl bereikt een draaiende frontend niet.** De ApplicationSet
ignore-difft de `GATSBY_`-env zodat devs live kunnen bijstellen, en daardoor
komt een gewijzigd thema pas aan op een verse frontend. Host, certificaat,
frontend-versie en apps gelden wél direct. Het scherm zegt dit ook.

**Een leeg thema is de veilige waarde.** Dan valt de ApplicationSet terug op
`conduction-theme`, dat in de gebundelde thema's zit en gegarandeerd rendert.
Een classname die nergens bestaat levert een site zonder thema op, zonder
foutmelding.

## Niet elke omgeving is bewerkbaar

Het portaal genereert het tenantbestand opnieuw uit de formuliervelden. Draagt
een bestand iets wat het formulier niet kent — `hostname`, `namespace`,
`features`, `resources`, `frontend.env` — dan zou dat verdwijnen. Zulke
omgevingen worden alleen getoond, met de gevonden sleutels erbij; wijzigen
doe je dan rechtstreeks in Nextcloud-base.

Van de 78 tenantbestanden zijn er 59 bewerkbaar (meting 2026-08-07).

## Certificaat

Zie [custom-domain-cert.md](custom-domain-cert.md). Kort: bij een eigen host
met `issuer: none` upload je het certificaat en de sleutel op ditzelfde
scherm; het portaal controleert het paar en plaatst het in het cluster.
