# Certificados intermedios adicionales

Algunos sitios (p. ej. buscadordeempleo.gov.co) no envian el certificado intermedio de su HTTPS.
Windows y los navegadores lo descargan solos; Python no, y la conexion falla con
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`.

Cada archivo `.pem` de esta carpeta se agrega a los certificados de confianza del worker
(ademas de los normales de certifi). Son certificados PUBLICOS de autoridades certificadoras:
no son secretos y se pueden versionar.

Para agregar el intermedio de un sitio (ejemplo con el SPE):

```bash
SITIO=www.buscadordeempleo.gov.co
URL=$(openssl s_client -connect $SITIO:443 -servername $SITIO </dev/null 2>/dev/null \
      | openssl x509 -noout -ext authorityInfoAccess | grep -o 'http[^ ,]*' | grep -iv ocsp | head -1)
curl -s "$URL" -o /tmp/intermedio
openssl x509 -inform DER -in /tmp/intermedio -out backend/worker/certificados/spe.pem 2>/dev/null \
  || openssl x509 -in /tmp/intermedio -out backend/worker/certificados/spe.pem
```
