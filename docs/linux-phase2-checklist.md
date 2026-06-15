# Checklist Linux Fase 2

La instancia `App` ejecuta NimbusCore y actua como headnode del cluster Linux.

## Accesos Informados

```text
Gateway: ssh ubuntu@10.20.11.13
App:     ssh ubuntu@10.20.11.195
App red Linux interna: 10.0.10.5

Linux:
server1: ssh ubuntu@10.20.11.13 -p 5811
         IP interna desde App: 10.0.10.1
server2: ssh ubuntu@10.20.11.13 -p 5812
         IP interna desde App: 10.0.10.2
server3: ssh ubuntu@10.20.11.13 -p 5813
         IP interna desde App: 10.0.10.3
server4: ssh ubuntu@10.20.11.13 -p 5814
         IP interna desde App: 10.0.10.4
ovs1:    ssh ubuntu@10.20.11.13 -p 5815
```

## Variables Linux Actuales

La configuracion base del proyecto apunta a App como headnode Linux y a cuatro workers:

```text
NIMBUSCORE_HEADNODE_IP=10.0.10.5
NIMBUSCORE_COMPUTE_IPS=10.0.10.1,10.0.10.2,10.0.10.3,10.0.10.4
```

La IP externa `10.20.11.195` se usa para entrar a App desde tu local. Los scripts usan la red interna `10.0.10.0/24`.

## Validaciones Minimas En App

```bash
ssh ubuntu@10.0.10.5 hostname
ssh ubuntu@10.0.10.1 hostname
ssh ubuntu@10.0.10.2 hostname
ssh ubuntu@10.0.10.3 hostname
ssh ubuntu@10.0.10.4 hostname
```

Desde el contenedor:

```bash
sudo docker compose exec linux-driver ssh ubuntu@10.0.10.5 hostname
sudo docker compose exec linux-driver ssh ubuntu@10.0.10.1 hostname
```

## Tunel Web Desde Local

```bash
ssh -N -L 8080:localhost:8080 ubuntu@10.20.11.195
```

Abrir:

```text
http://localhost:8080/login.html
```

## Punto Importante Sobre noVNC

Para noVNC, el contenedor `console-access` necesita llegar por TCP al host/puerto VNC de la VM.

Por eso `NIMBUSCORE_COMPUTE_IPS` usa IPs directas/ruteables desde App hacia server1-4: `10.0.10.1-10.0.10.4`.
