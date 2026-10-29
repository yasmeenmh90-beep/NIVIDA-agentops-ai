import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import type { Group } from "three";

function Rig() {
  const ref = useRef<Group>(null);
  useFrame((_, delta) => {
    if (document.hidden || !ref.current) return;
    ref.current.rotation.y += delta * 0.16;
    ref.current.rotation.x = Math.sin(ref.current.rotation.y * 0.7) * 0.12;
  });
  return (
    <group ref={ref}>
      <mesh>
        <icosahedronGeometry args={[1.05, 1]} />
        <meshStandardMaterial color="#142000" emissive="#76B900" emissiveIntensity={0.45} wireframe />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.28, 24, 16]} />
        <meshStandardMaterial color="#76B900" emissive="#76B900" emissiveIntensity={0.9} />
      </mesh>
      <mesh rotation={[Math.PI / 2.35, 0.15, 0]}>
        <torusGeometry args={[1.65, 0.012, 8, 72]} />
        <meshBasicMaterial color="#76B900" />
      </mesh>
      <mesh rotation={[0.7, 0.5, 0.15]}>
        <torusGeometry args={[2.15, 0.008, 8, 72]} />
        <meshBasicMaterial color="#A6AEA2" transparent opacity={0.4} />
      </mesh>
      <points>
        <sphereGeometry args={[2.35, 16, 10]} />
        <pointsMaterial color="#76B900" size={0.02} sizeAttenuation />
      </points>
    </group>
  );
}

/** Decorative incident-system model. The product explanation lives in the page copy. */
export default function HeroScene() {
  return (
    <Canvas
      camera={{ position: [0, 0.15, 5.4], fov: 40 }}
      dpr={[1, 1.5]}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
    >
      <ambientLight intensity={0.4} />
      <pointLight position={[2.5, 1.6, 3]} intensity={12} color="#76B900" distance={12} />
      <Rig />
    </Canvas>
  );
}
