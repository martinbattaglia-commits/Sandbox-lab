# Sellbird para Logística TOPS

Entorno de prueba de Verotín S.A. en `Sandbox-lab/cellbird-logisticatops/`. El proyecto oficial se llama Sellbird y mantiene el comando legado `harvey`. Se usó Sellbird 0.4.0 en el [commit b212385](https://github.com/ethanplusai/sellbird/tree/b212385259da92246a56311d790d936e75dd70dd), Python 3.12.14 y Codex CLI 0.159.3. El código original, el entorno virtual y las bases locales se excluyen del repositorio.

## Instalación en una VM nueva

Desde esta subcarpeta, si todavía no existen `upstream/` y `venv/`:

```bash
git clone https://github.com/ethanplusai/sellbird.git upstream
git -C upstream checkout --detach b212385259da92246a56311d790d936e75dd70dd
mkdir -p .runtime/tmp
mv upstream/.git .runtime/upstream-git
python3 -m venv venv
source venv/bin/activate
python -m pip install ./upstream
npm_config_cache="$PWD/.runtime/npm-cache" npm install --prefix "$PWD/.runtime/codex-cli" --no-audit --no-fund @openai/codex@0.159.3
```

En cada sesión, desde esta subcarpeta:

```bash
source venv/bin/activate
export SELLBIRD_HOME="$PWD"
export PATH="$PWD/.runtime/codex-cli/node_modules/.bin:$PATH"
export TMPDIR="$PWD/.runtime/tmp"
mkdir -p "$TMPDIR"
test -L sellbird.local.yaml || ln -s cellbird.local.yaml sellbird.local.yaml
sellbird sending pause
sellbird signals --confirm free
sellbird inboxes list
sellbird outbox
sellbird status --check
```

Sellbird lee `sellbird.local.yaml`. El enlace local apunta a `cellbird.local.yaml`, que es el archivo versionado. La app instala sus plantillas base en el directorio de trabajo cuando faltan. Se versionan las fichas y el prompt personalizados. Usá un directorio de trabajo desechable dentro de `.runtime/` para cada ejecución futura de `sellbird train <url> 3 --provider codex`. El comando acepta una URL por vez y puede reemplazar campos aprendidos y el archivo de configuración local. Revisá y trasladá los resultados de forma selectiva.

## Configuración y seguridad

El correo y LinkedIn están deshabilitados. El límite diario es cero, la aprobación es obligatoria y el interruptor quedó pausado en la base local. La identidad de correo de prueba usa `example.invalid`. El horario configurado es lunes a viernes de 09:00 a 17:00 en Buenos Aires. El pie incluye la dirección postal y una baja en español aceptada por el validador. Las pruebas se ejecutaron mediante comandos de diagnóstico y componentes individuales del pipeline.

## Resultados del 23 de noviembre de 2026

| Etapa | Resultado |
|---|---|
| Entrenamiento nativo | Se intentaron las tres URLs por separado con el rastreador HTTP incorporado. Cada una devolvió cero páginas. El chequeo independiente de la CLI exigió renovar la sesión de ChatGPT. Las fichas versionadas combinan el brief y las tres páginas públicas consultadas por separado. La de competencia describe alternativas operativas sin adjudicar condiciones a empresas concretas. |
| Señales | 29 en catálogo, 26 gratuitas confirmadas. Quedaron pendientes `MAPS_RANK`, `SERP_RANK` y `EMAIL_STATUS`. |
| OpenStreetMap | Dos consultas para mayoristas y farmacias con radio de 15 km desde el centro de CABA, límite de 8 por consulta y gasto máximo USD 0. Ambas instancias Overpass devolvieron HTTP 403. Cero consultas exitosas, empresas, sitios perfilados y observaciones reales. Gasto USD 0. |
| Perfilado de control | Un sitio HTML sintético de una distribuidora ficticia procesado por el perfilador de Sellbird, con 8 observaciones guardadas. |
| Outbox de control | El Writer de Sellbird recibió una respuesta de modelo simulada. Un correo a `ana@example.invalid` quedó en `pending_review`. Cero aprobados, cero enviados y ninguna llamada al transporte. El buzón figuró como no conectado. |

Los CSV de `exports/` registran los conteos y el borrador sintético. La base de datos temporal y los registros detallados permanecen locales.

## Descubrimiento y producción

Estimaciones en USD mostradas por `sellbird discover --providers` para la versión instalada. Se pueden recalcular con `--estimate`, que no consulta el proveedor comercial.

| Proveedor | Estimación de Sellbird |
|---|---:|
| OpenStreetMap | 0, sin cuenta |
| TinyFish | 0 con clave, hasta 30 búsquedas/min y 500/h |
| DataForSEO Business Listings | 0,372 por 1.000 fichas |
| DataForSEO SERP | 0,006 por búsqueda Live a profundidad 30 |
| DataForSEO Google Maps | 0,002 por búsqueda Live de hasta 100 resultados |
| Serper | 0,30 a 1,00 por 1.000 búsquedas, con 2.500 iniciales gratis según Sellbird |
| Scavio Google Maps | 0,01 por página de hasta 20 fichas |
| Scavio Google Search | Aproximadamente 0,03 por búsqueda a profundidad 30 |

DataForSEO informa un depósito mínimo de USD 50 para pasar a uso real en el catálogo de Sellbird. Confirmá las condiciones con el proveedor antes de contratar. No se ejecutaron consultas con clave externa.

Para importadores, fabricantes y distribuidores industriales, el primer candidato es TinyFish por frases con rubro y localidad. Complementalo con padrones públicos o sectoriales y validación del sitio de cada empresa. Serper es una segunda opción si se necesita observar posición orgánica. OpenStreetMap resulta más apropiado para establecimientos físicos identificables y como fuente de contraste.

Antes de una prueba real de Codex, renovar la sesión de ChatGPT con la CLI y verificar `sellbird status --check`. Para producción, confirmar la casilla dedicada, el dominio o subdominio secundario y su DNS, el proveedor de verificación de emails y su presupuesto, y la lista de exclusión de clientes y competidores. Mantener el envío pausado hasta contar con aprobación expresa.

- [Documentación oficial de Codex en Sellbird](https://github.com/ethanplusai/sellbird/blob/b212385259da92246a56311d790d936e75dd70dd/docs/codex.md)
- [Catálogo y pipeline de señales](https://github.com/ethanplusai/sellbird/blob/b212385259da92246a56311d790d936e75dd70dd/docs/signals-and-pipeline.md)

## Calificación y borradores

El exportador de Nexus y el Writer de prueba usan las rúbricas en [Calificación comercial](skills/lead_qualification.md). Para obtener un CSV local y comprobar quién pasa el umbral sin generar borradores, desde esta subcarpeta:

```bash
venv/bin/python scripts/export_to_nexus_csv.py
venv/bin/python scripts/write_qualified_drafts.py --dry-run
```

Para generar borradores con la clasificación obligatoria, usá `venv/bin/python scripts/write_qualified_drafts.py` con el entorno de Codex configurado como se indica arriba. Este comando ejecuta únicamente el Writer. El heartbeat original de Sellbird usa el puntaje de su Scout y queda fuera del flujo de borradores de TOPS. El Writer local exige envío pausado, cupo cero y revisión manual.
