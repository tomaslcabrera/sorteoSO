# simulacion_sorteo_locks.py
# Paso de mensajes SÍNCRONO con canales (rendezvous) usando threading + Lock/Condition

import threading
import random
import time
from dataclasses import dataclass
from typing import Dict, List

# ================== Canal síncrono (rendezvous) ==================
class RendezvousChannel:
    """
    Canal SÍNCRONO (sin buffer) estilo Pascal-FC:
    - send(x): bloquea hasta que un recv() empareje y ACKee.
    - recv(): bloquea hasta que haya un send() esperando.
    Implementado con un monitor (Lock + Conditions) y hand-shake.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._can_send = threading.Condition(self._lock)  # hay receptor esperando
        self._can_recv = threading.Condition(self._lock)  # hay mensaje disponible
        self._ack      = threading.Condition(self._lock)  # ack del receptor al emisor

        self._has_msg = False
        self._msg = None
        self._waiting_receivers = 0  # cuántos recv() están listos para emparejar

    def send(self, value):
        with self._lock:
            # Esperar hasta que exista (al menos) un receptor esperando
            # y hasta que NO haya mensaje en tránsito (rendezvous 1 a 1)
            while self._waiting_receivers == 0 or self._has_msg:
                self._can_send.wait()

            # Colocar el mensaje y avisar a un receptor
            self._msg = value
            self._has_msg = True
            self._can_recv.notify()

            # Esperar el ACK del receptor (no retornamos antes)
            while self._has_msg:
                self._ack.wait()

    def recv(self):
        with self._lock:
            # Declararse "receptor listo" y avisar a posibles emisores
            self._waiting_receivers += 1
            self._can_send.notify()

            # Esperar a que haya mensaje en tránsito
            while not self._has_msg:
                self._can_recv.wait()

            # Tomar el mensaje (rendezvous)
            val = self._msg
            self._msg = None
            self._has_msg = False
            self._waiting_receivers -= 1

            # Enviar ACK al emisor y permitir a otro emisor avanzar
            self._ack.notify()
            self._can_send.notify()
            return val

# ================== Mensajes ==================
@dataclass(frozen=True)
class Submission:
    jugador_id: int
    numero: int

@dataclass(frozen=True)
class ResultadoJugador:
    elegido: int
    agraciado: int
    acierto: bool

# ================== Procesos ==================
def jugador(jid: int, ch_envio: RendezvousChannel, ch_notif: RendezvousChannel):
    # Llegada aleatoria al "mostrador"
    time.sleep(random.uniform(0.0, 1.0))
    elegido = random.randint(1, 20)
    print(f"[Jugador {jid}] Elijo {elegido} y lo envío a Administración.")
    ch_envio.send(Submission(jid, elegido))  # rendezvous con Admin

    # Más tarde, el jugador “se pone disponible” para escuchar su resultado
    time.sleep(random.uniform(0.0, 2.0))
    res: ResultadoJugador = ch_notif.recv()  # rendezvous con Admin
    estado = "¡ACERTÉ!" if res.acierto else "no acerté"
    print(f"[Jugador {jid}] Elegido={res.elegido}, Agraciado={res.agraciado} -> {estado}")

def sorteo(ch_sorteo_admin: RendezvousChannel):
    time.sleep(random.uniform(0.5, 1.5))
    agraciado = random.randint(1, 20)
    print(f"[Sorteo] Número agraciado es {agraciado}. Lo envío a Administración.")
    ch_sorteo_admin.send(agraciado)  # rendezvous

def escrutinio(ch_admin_escru: RendezvousChannel, ch_escru_admin: RendezvousChannel):
    numeros_por_jugador, agraciado = ch_admin_escru.recv()  # rendezvous
    print(f"[Escrutinio] Recibí {len(numeros_por_jugador)} envíos. Agraciado={agraciado}.")
    resultados: Dict[int, ResultadoJugador] = {
        jid: ResultadoJugador(elegido=num, agraciado=agraciado, acierto=(num == agraciado))
        for jid, num in numeros_por_jugador.items()
    }
    ch_escru_admin.send(resultados)  # rendezvous
    print(f"[Escrutinio] Resultados enviados a Administración.")

def administracion(n_jugadores: int,
                   ch_envios: RendezvousChannel,
                   ch_sorteo_admin: RendezvousChannel,
                   ch_admin_escru: RendezvousChannel,
                   ch_escru_admin: RendezvousChannel,
                   canales_notif: List[RendezvousChannel]):
    print("[Admin] Esperando números de los jugadores...")
    recibidos: Dict[int, int] = {}

    # Recibir 1 (y solo 1) número por jugador, en cualquier orden
    while len(recibidos) < n_jugadores:
        sub: Submission = ch_envios.recv()  # disponible para “cualquiera”
        if sub.jugador_id not in recibidos:
            recibidos[sub.jugador_id] = sub.numero
            print(f"[Admin] Recibí de J{sub.jugador_id}: {sub.numero} "
                  f"({len(recibidos)}/{n_jugadores})")
        else:
            # Funcionalmente “impide” reenvío: no altera la tabla (idempotente)
            print(f"[Admin] Ignoro duplicado de J{sub.jugador_id}.")

    # Esperar número agraciado del proceso Sorteo
    print("[Admin] Tengo todos los números. Espero el número agraciado...")
    agraciado = ch_sorteo_admin.recv()
    print(f"[Admin] Agraciado recibido: {agraciado}. Envío a Escrutinio.")
    ch_admin_escru.send((recibidos, agraciado))

    # Recibir resultados del Escrutinio
    resultados: Dict[int, ResultadoJugador] = ch_escru_admin.recv()
    print("[Admin] Resultados recibidos. Notifico a los jugadores...")

    # Notificar “según disponibilidad” y sin orden (barajamos los ids)
    orden = list(resultados.keys())
    random.shuffle(orden)
    for jid in orden:
        canales_notif[jid].send(resultados[jid])  # rendezvous con cada jugador
        print(f"[Admin] Notifiqué a J{jid}.")

# ================== Orquestación ==================
def main():
    random.seed()  # fija una semilla (p.ej. 42) para reproducibilidad si querés
    N = 10

    # Canales
    ch_envios = RendezvousChannel()          # Jugadores -> Admin
    ch_sorteo_admin = RendezvousChannel()    # Sorteo -> Admin
    ch_admin_escru = RendezvousChannel()     # Admin -> Escrutinio
    ch_escru_admin = RendezvousChannel()     # Escrutinio -> Admin
    ch_notif = [None] + [RendezvousChannel() for _ in range(N)]  # 1..N

    # Threads
    threads: List[threading.Thread] = []
    threads.append(threading.Thread(target=administracion,
                                    args=(N, ch_envios, ch_sorteo_admin,
                                          ch_admin_escru, ch_escru_admin, ch_notif),
                                    name="Administracion"))
    threads.append(threading.Thread(target=sorteo, args=(ch_sorteo_admin,), name="Sorteo"))
    threads.append(threading.Thread(target=escrutinio,
                                    args=(ch_admin_escru, ch_escru_admin),
                                    name="Escrutinio"))

    for jid in range(1, N + 1):
        threads.append(threading.Thread(target=jugador,
                                        args=(jid, ch_envios, ch_notif[jid]),
                                        name=f"Jugador-{jid}"))

    # Lanzar y esperar
    for t in threads: t.start()
    for t in threads: t.join()
    print("Simulación finalizada.")

if __name__ == "__main__":
    main()
