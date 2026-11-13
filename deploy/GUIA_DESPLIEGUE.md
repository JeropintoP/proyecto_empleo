# Despliegue en Oracle Cloud (Always Free)

Resultado: la aplicación en `https://<subdominio>.duckdns.org`, encendida 24/7, con HTTPS,
actualización diaria a las 6:00 a.m. y respaldo diario de la base de datos.

---

## 1. Cuenta de Oracle Cloud

1. Regístrese en oracle.com/cloud/free. Piden tarjeta solo para verificar identidad.
2. **Región de origen (home region):** no se puede cambiar después. Elija una con instancias
   **Ampere A1** disponibles (por ejemplo, US East – Ashburn o Brazil East – São Paulo).

## 2. Llave SSH (en su Fedora)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oracle_empleo -C "proyecto-empleo"
cat ~/.ssh/oracle_empleo.pub     # esta es la que se pega en Oracle
```

## 3. Crear la máquina

Consola de Oracle → **Compute → Instances → Create instance**

| Campo | Valor |
|---|---|
| Nombre | `proyecto-empleo` |
| Image | **Canonical Ubuntu 24.04** (aarch64) |
| Shape | **Ampere → VM.Standard.A1.Flex**, 2 OCPU, 12 GB |
| Networking | VCN nueva, subred pública, **Assign public IPv4 address** |
| SSH keys | *Paste public key* → contenido de `oracle_empleo.pub` |

Si aparece *Out of capacity*, intente más tarde u otro *availability domain*.
Anote la **IP pública**.

## 4. Abrir puertos 80 y 443 en Oracle

Instancia → **Subnet → Security List → Add Ingress Rules** (dos reglas):

| Source CIDR | Protocolo | Puerto destino |
|---|---|---|
| `0.0.0.0/0` | TCP | 80 |
| `0.0.0.0/0` | TCP | 443 |

## 5. Dominio gratuito (DuckDNS)

1. Entre a duckdns.org con su cuenta de GitHub.
2. Cree un subdominio, por ejemplo `proyectoempleo`.
3. En *current ip* ponga la IP pública de la instancia y presione **update ip**.

## 6. Preparar el servidor

```bash
ssh -i ~/.ssh/oracle_empleo ubuntu@<IP_PUBLICA>

git clone https://github.com/JeropintoP/proyecto_empleo.git
cd proyecto_empleo
bash deploy/instalar_servidor.sh
exit                                          # salir y volver a entrar
ssh -i ~/.ssh/oracle_empleo ubuntu@<IP_PUBLICA>
```

> **Si el repositorio es privado**, antes del `git clone`:
> ```bash
> ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
> cat ~/.ssh/github_deploy.pub
> ```
> Pegue esa llave en GitHub → repositorio → **Settings → Deploy keys → Add deploy key** (solo lectura) y clone con:
> `GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" git clone git@github.com:JeropintoP/proyecto_empleo.git`

## 7. Variables de entorno

```bash
cd ~/proyecto_empleo
cp .env.example .env
openssl rand -hex 24     # úsela como clave de PostgreSQL
openssl rand -hex 32     # úsela como JWT_SECRETO
nano .env
chmod 600 .env
```

| Variable | Valor |
|---|---|
| `DOMINIO` | `proyectoempleo.duckdns.org` (el suyo) |
| `POSTGRES_PASSWORD` | la primera clave generada |
| `DATABASE_URL` | reemplace `cambiar_por_una_clave_larga` por esa misma clave |
| `JWT_SECRETO` | la segunda clave generada |
| `GROQ_API_KEY` | su key de Groq |
| `IMAP_CLAVE_APLICACION` | contraseña de aplicación de dstogether01 (sin espacios) |

## 8. Levantar

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps                       # todos en "running"
curl -s https://proyectoempleo.duckdns.org/api/salud
```

La primera vez tarda unos minutos (compila Angular y obtiene el certificado HTTPS).

## 9. Usuarios

```bash
docker compose exec api python -m app.cli crear-usuario --nombre "Jerónimo Pinto" \
    --email luisjeronimopinto@gmail.com --alias jeronimopinto
docker compose exec api python -m app.cli crear-usuario --nombre "Helen Visbal" \
    --email <correo de Helen> --alias helenvisbal
```

Luego, cada uno entra a la web, va a **Mi perfil** y sube su hoja de vida.

## 10. Primera recolección (sin esperar a las 6 a.m.)

```bash
docker compose exec worker python -m worker.main --ahora
docker compose logs worker --tail 30
```

## 11. Respaldo diario

```bash
mkdir -p ~/respaldos
crontab -e
# agregar esta línea:
30 5 * * * /home/ubuntu/proyecto_empleo/deploy/respaldo.sh >> /home/ubuntu/respaldos/respaldo.log 2>&1
```

Restaurar un respaldo:
```bash
gunzip -c ~/respaldos/proyecto_empleo_AAAA-MM-DD.sql.gz | docker compose exec -T db psql -U empleo -d proyecto_empleo
```

## 12. Actualizar la aplicación

```bash
cd ~/proyecto_empleo && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## 13. Inactividad (importante)

Oracle puede **detener** instancias gratuitas que pasan 7 días con CPU, red y memoria por
debajo del 20 %. Esta aplicación usa muy pocos recursos, así que puede pasar.

- Si sucede, Oracle avisa por correo; la instancia se puede volver a encender y los datos se conservan.
- Para evitarlo del todo, Oracle sugiere convertir la cuenta a **Pay As You Go**: los recursos
  Always Free siguen sin costo, pero dejan de estar sujetos a esa regla. El riesgo es que,
  si por error se crea un recurso fuera del plan gratuito, sí se cobra. Si lo hace, configure
  un **presupuesto con alerta** de 1 USD en *Billing → Budgets*.

## Problemas comunes

| Síntoma | Revisar |
|---|---|
| La web no abre | Reglas de la Security List (paso 4) y `sudo iptables -L INPUT -n` |
| Error de certificado HTTPS | Que DuckDNS apunte a la IP correcta; `docker compose logs caddy` |
| No llegan vacantes del correo | `docker compose logs worker`; clave de aplicación sin espacios |
| La IA no responde | `GROQ_API_KEY`; tabla `uso_ia` (columna `error`) |
