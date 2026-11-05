# norma43-mcp

MCP para **extractos bancarios españoles Norma 43** (Cuaderno 43 / AEB43 / CSB43) y **conciliación bancaria** para agentes de IA. Cobro por créditos con [KeyFlint](https://keyflint.com).

Todos los bancos españoles exportan Norma 43; gestorías y departamentos financieros concilian cada mes. Este servidor lee el fichero como lo valida un banco y cruza movimientos con facturas sin adivinar nunca.

## Herramientas

| Tool | Créditos | Qué hace |
|---|---|---|
| `parse_norma43` | 1 | Cuentas, IBAN calculado, banco, titular, saldos, movimientos, textos 23 y campos SEPA (modalidad 3). Comprueba totales del registro 33, saldo inicial + movimientos = final, recuento del 88, fechas, signos y formatos. |
| `norma43_to_csv` | 1 | CSV listo para Excel español (`;`, coma decimal), protegido contra inyección de fórmulas. |
| `reconcile_movements` | 3 | Conciliación 1:1, 1:N (una transferencia paga varias facturas) y N:1 (pagos parciales). Puntuación 0-100, confianza y evidencias. Empates → `ambiguous`. |
| `validate_identifier` | 0 | IBAN, CCC, NIF/NIE/CIF, identificador de acreedor SEPA, Referencia 1. |
| `sepa_creditor_id` | 0 | Identificador de acreedor SEPA (AT-02) a partir del NIF. |
| `pricing`, `credits_balance` | 0 | Precios y saldo de la licencia. |

Precios configurables: `NORMA43_PRICES="parse_norma43=2,reconcile_movements=5"`.

Importes: cadenas con 2 decimales; negativo = cargo (sale dinero), positivo = abono (entra).

## Instalación

Solo necesitas tu licencia KeyFlint (`LIC-...`). La URL del gateway, su clave pública y el enlace de compra vienen por defecto.

**Claude Desktop, 1 clic:** descarga `norma43-mcp-X.Y.Z.mcpb` de la [última release](https://github.com/ArceeDev/norma43-mcp/releases/latest), ábrelo con doble clic y pega tu licencia. Sin terminal ni JSON.

**Claude Code:**

```bash
claude mcp add norma43 -e KEYFLINT_LICENSE=LIC-... -- uvx norma43-mcp
```

**Cursor, Windsurf, Claude Desktop (JSON):**

```json
{
  "mcpServers": {
    "norma43": {
      "command": "uvx",
      "args": ["norma43-mcp"],
      "env": { "KEYFLINT_LICENSE": "LIC-..." }
    }
  }
}
```

Opcionales: `KEYFLINT_URL`, `KEYFLINT_PUBLIC_KEYS` (si no se pone, se descarga del gateway y se guarda en `~/.keyflint/keyring.json`), `KEYFLINT_CHECKOUT_URL`.

## Modo hosted (HTTP, licencia por petición)

```bash
docker build -t norma43-mcp .
docker run -p 8080:8080 \
  norma43-mcp
```

El cliente envía `Authorization: Bearer LIC-...` (o `X-License-Key`).

Variables: `NORMA43_TRANSPORT` (`stdio`|`http`), `NORMA43_PRICES`, `NORMA43_MAX_BYTES` (por defecto 5 MB), `PORT`, `HOST`.

## Flujo de cobro por llamada

1. Se decodifica y valida la entrada. **Entrada ilegible u opciones inválidas: no se cobra.**
2. Se verifica la licencia (Ed25519, offline) antes de gastar CPU.
3. Se parsea el fichero.
4. Se descuentan los créditos (idempotente).
5. Si la herramienta falla después del cobro, **se reembolsa**.
6. Sin créditos: error `-32062` con la URL de compra.

## Reglas de conciliación

- Céntimos enteros, sin coma flotante.
- Evidencias: importe exacto (o dentro de `amount_tolerance`), referencia de factura en el concepto bancario, nombre de la contraparte, distancia de fechas y unicidad.
- Una referencia solo cuenta con ≥4 caracteres; las numéricas, con ≥6 dígitos y límites de dígito.
- Agrupaciones (1:N, N:1) solo si la suma exacta es única y hay evidencia de referencia o nombre; si no, van a `suggestions`.
- Dos candidatos con la misma puntuación nunca se resuelven: van a `ambiguous` para revisión humana.

## Limitaciones

- La interpretación SEPA de los registros 23 (Anexo 4) solo se aplica en modalidad 3; en las demás se devuelve el texto tal cual.
- El nombre del banco es informativo (tabla de las entidades principales).
- Validado contra la especificación oficial (AEB/CECA/UNACC, junio 2012) y sus ejemplos, con ficheros generados por un escritor independiente; conviene probarlo con extractos reales de cada banco.

## Tests

```bash
pip install -e ".[test]"
python -m pytest -q
```

## Licencia

Business Source License 1.1. Código visible; el uso en producción requiere una licencia KeyFlint (o ser no comercial). Pasa a Apache 2.0 cuatro años después de cada versión. Ver [LICENSE](LICENSE).
