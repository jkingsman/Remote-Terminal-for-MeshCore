import { getContactAvatar } from '../utils/contactAvatar';

interface ContactAvatarProps {
  name: string | null;
  publicKey: string;
  size?: number;
  contactType?: number;
}

export function ContactAvatar({ name, publicKey, size = 28, contactType }: ContactAvatarProps) {
  const avatar = getContactAvatar(name, publicKey, contactType);

  return (
    <div
      className="flex items-center justify-center rounded-full font-semibold flex-shrink-0 select-none"
      style={{
        backgroundColor: avatar.background,
        color: avatar.textColor,
        width: size,
        height: size,
        fontSize: size * 0.45,
      }}
    >
      {avatar.text}
    </div>
  );
}
